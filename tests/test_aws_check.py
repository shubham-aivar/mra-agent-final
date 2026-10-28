import importlib.util

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from mra.adapters.aws_storage import AwsStorage
from mra.ports import ChatReply, LlmReply
from tests.conftest import ROOT

spec = importlib.util.spec_from_file_location("aws_check", ROOT / "scripts" / "aws_check.py")
chk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chk)


class Sts:
    def get_caller_identity(self):
        return {"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/Admin/me"}


class GoodLlm:
    model_id = "fake"

    def complete(self, system, user, max_tokens):
        return LlmReply('```json\n{"ok": true}\n```', "end_turn", 20, 5, "fake")

    def chat(self, system, messages, tools, max_tokens):
        if len(messages) == 1:
            return ChatReply([{"text": "Calling."}, {"toolUse": {"toolUseId": "u1", "name": "echo", "input": {"text": "hello"}}}],
                             "tool_use", 30, 8)
        return ChatReply([{"text": "The tool returned hello."}], "end_turn", 40, 6)


@pytest.fixture
def s3(monkeypatch):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "test")
    with mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        c.create_bucket(Bucket="mra-check-bucket")
        yield c


def by_name(results):
    return {n: (s, d) for n, s, d in results}


def test_everything_passes_and_the_probe_leaves_nothing_behind(s3):
    s3.put_public_access_block(Bucket="mra-check-bucket", PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    res = by_name(chk.run_checks(Sts(), GoodLlm(), s3, AwsStorage("mra-check-bucket", "us-east-1", client=s3),
                                 "mra-check-bucket", False, False))
    assert {n: s for n, (s, _) in res.items()} == {"identity": "PASS", "bedrock text": "PASS", "bedrock tool use": "PASS",
                                                   "s3 bucket": "PASS", "s3 read/write": "PASS", "s3 no-overwrite write": "PASS",
                                                   "s3 cleanup": "PASS", "s3 encryption": "INFO", "s3 public access": "PASS"}
    assert "account ending 9012 as an assumed role" in res["identity"][1] and "123456789012" not in str(res)   # never prints the full id
    assert s3.list_objects_v2(Bucket="mra-check-bucket").get("KeyCount", 0) == 0


def test_an_open_bucket_is_reported_as_a_failure(s3):
    res = by_name(chk.run_checks(Sts(), GoodLlm(), s3, AwsStorage("mra-check-bucket", "us-east-1", client=s3),
                                 "mra-check-bucket", True, False))
    assert res["s3 public access"][0] == "FAIL" and "bedrock text" not in res


def test_model_that_ignores_the_tool_is_a_clear_failure():
    class Chatty(GoodLlm):
        def chat(self, system, messages, tools, max_tokens):
            return ChatReply([{"text": "Sure, hello!"}], "end_turn", 5, 5)
    s, d = chk.check_bedrock_tools(Chatty())[0][1:]
    assert s == "FAIL" and "did not ask for the tool" in d


def test_a_model_that_never_finishes_after_the_tool_result_fails():
    class Loops(GoodLlm):
        def chat(self, system, messages, tools, max_tokens):
            return ChatReply([{"toolUse": {"toolUseId": "u", "name": "echo", "input": {"text": "hello"}}}], "tool_use", 5, 5)
    assert "did not finish" in chk.check_bedrock_tools(Loops())[0][2]


def test_a_malformed_tool_request_fails():
    class Bad(GoodLlm):
        def chat(self, system, messages, tools, max_tokens):
            return ChatReply([{"toolUse": {"toolUseId": "u", "name": "echo", "input": {"wrong": 1}}}], "tool_use", 5, 5)
    s, d = chk.check_bedrock_tools(Bad())[0][1:]
    assert s == "FAIL" and "malformed" in d


def test_text_check_fails_on_an_unexpected_reply():
    class Off(GoodLlm):
        def complete(self, system, user, max_tokens):
            return LlmReply('{"ok": false}', "end_turn", 5, 5, "fake")
    assert chk.check_bedrock_text(Off())[0][1] == "FAIL"


@pytest.mark.parametrize("code,needle", [("ExpiredTokenException", "aws sso login"), ("AccessDeniedException", "Model access"),
                                         ("ValidationException", "MRA_MODEL_ID"), ("ThrottlingException", "wait a minute"),
                                         ("SomethingNew", "unexpected error")])
def test_failures_come_with_a_plain_hint_and_stop_early_when_not_signed_in(code, needle):
    err = ClientError({"Error": {"Code": code, "Message": "boom"}}, "Converse")

    class Boom(GoodLlm):
        def complete(self, system, user, max_tokens):
            raise err
    res = chk.run_checks(Sts(), Boom(), None, None, None, False, True)
    text = dict((n, d) for n, s, d in res)["bedrock text"]
    assert code in text and needle.lower() in text.lower()

    class NoSts:
        def get_caller_identity(self):
            raise err
    res = chk.run_checks(NoSts(), Boom(), None, None, None, False, True)
    assert [r[0] for r in res] == ["identity"] and res[0][1] == "FAIL"


def test_s3_is_skipped_without_a_bucket_and_can_be_switched_off():
    assert ("s3", "SKIP", "no --bucket given") in chk.run_checks(Sts(), GoodLlm(), None, None, None, False, False)
    assert all(r[0] != "s3" for r in chk.run_checks(Sts(), GoodLlm(), None, None, None, False, True))
