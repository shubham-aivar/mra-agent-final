import boto3
import pytest
from moto import mock_aws

from mra.adapters.aws_storage import AwsStorage
from mra.adapters.llm_stub import StubLlm
from mra.service import AssessmentService
from mra.settings import Settings
from tests.conftest import fixed_clock, make_raw


@pytest.fixture
def s3(monkeypatch):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "test")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="mra-test-bucket")
        yield AwsStorage("mra-test-bucket", "us-east-1")


def test_same_contract_as_local_storage(s3):
    s3.put("a/b.txt", b"1")
    assert s3.get("a/b.txt") == b"1" and s3.exists("a/b.txt") and not s3.exists("a/c.txt")
    s3.put("a/c.txt", b"x")
    assert s3.list("a/") == ["a/b.txt", "a/c.txt"] and s3.list("zzz") == []
    with pytest.raises(FileNotFoundError):
        s3.get("nope")
    assert s3.delete_prefix("a/") == 2 and s3.list("") == []


def test_delete_prefix_handles_more_than_one_batch(s3):
    for i in range(1005):
        s3.put(f"many/{i:05d}", b"x")
    assert s3.delete_prefix("many/") == 1005 and s3.list("many/") == []


def test_put_new_refuses_to_overwrite(s3):
    assert s3.put_new("k", b"first") is True
    assert s3.put_new("k", b"second") is False and s3.get("k") == b"first"


def test_whole_review_flow_runs_on_s3(s3, cfg, golden):
    from mra.loader import build_response_set
    svc = AssessmentService(s3, StubLlm(), cfg, Settings(), clock=fixed_clock())
    rs = build_response_set(make_raw(cfg, golden["ratings"]), cfg)
    svc.run(rs, "analyst")
    assert svc.verify_audit(rs.set_id)["ok"] and svc.status(rs.set_id)["actions_total"] == 24
    assert set(svc.export(rs.set_id, ["xlsx", "docx"], "tc")) == {f"MRA_{rs.set_id}_DRAFT.xlsx", f"MRA_{rs.set_id}_DRAFT.docx"}
    assert svc.purge(rs.set_id, "tc", True)["objects_removed"] > 10 and s3.list(f"runs/{rs.set_id}/") == []
