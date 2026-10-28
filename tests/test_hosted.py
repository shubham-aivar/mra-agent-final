"""The hosted Lambda entry point, with real S3 behaviour (moto) for presigned uploads and downloads, and a fake Lambda for the background jobs."""
import base64
import json

import boto3
import pytest
import requests
from botocore.config import Config
from moto import mock_aws

from hosted import web
from mra.adapters.aws_storage import AwsStorage
from mra.adapters.llm_stub import StubLlm
from mra.service import AssessmentService
from mra.settings import Settings
from mra.ui.api import UiApi
from mra.ui.jobs import JobQueue
from mra.ui.routes import Router
from tests.conftest import fixed_clock, write_questionnaire
from tests.helpers import NOTE

REVIEWER = {"email": "tc@aivar.tech", "cognito:groups": "[mra-reviewers]"}
READER = {"email": "rd@aivar.tech", "cognito:groups": "[]"}
OTHER = {"email": "other@aivar.tech", "cognito:groups": "[mra-reviewers]"}
BUCKET = "mra-test-bucket"


class FakeLambda:
    def __init__(self):
        self.sent, self.fail = [], False

    def invoke(self, **kw):
        if self.fail:
            raise RuntimeError("cannot invoke")
        self.sent.append(json.loads(kw["Payload"]))


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def host(cfg, monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "mra-agent")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "y")
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1", config=Config(signature_version="s3v4"))
        s3.create_bucket(Bucket=BUCKET)
        store = AwsStorage(BUCKET, "us-east-1", client=s3)
        svc = AssessmentService(store, StubLlm(), cfg, Settings(), clock=fixed_clock())
        fake, clock = FakeLambda(), Clock()
        router = Router(UiApi(svc, hosted=True), JobQueue(store, lambda p: fake.invoke(Payload=json.dumps(p).encode()), clock=clock), hosted=True)
        web.set_router(router, fake)

        def call(method, path, body=None, claims=REVIEWER, raw_body=None, b64=False, headers=None):
            ev = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": headers or {}}
            if claims is not None:
                ev["requestContext"]["authorizer"] = {"jwt": {"claims": claims}}
            if raw_body is not None:
                ev["body"] = raw_body
            elif body is not None:
                ev["body"] = base64.b64encode(json.dumps(body).encode()).decode() if b64 else json.dumps(body)
                ev["isBase64Encoded"] = b64
            r = web.handler(ev)
            return r["statusCode"], json.loads(r["body"]), r["headers"]

        def upload(data: bytes, purpose: str, name: str, claims=REVIEWER):
            st, out, _ = call("POST", "/api/uploads", {"purpose": purpose, "name": name, "size": len(data)}, claims=claims)
            assert st == 200 and out["mode"] == "post", out
            r = requests.post(out["url"], data=out["fields"], files={"file": (name, data)})
            assert r.status_code in (200, 201, 204), r.text
            return out["upload_id"]

        def run_jobs():
            sent, fake.sent = list(fake.sent), []
            return [web.handler(p) for p in sent]

        call.svc, call.fake, call.clock, call.upload, call.run_jobs, call.s3, call.store = svc, fake, clock, upload, run_jobs, s3, store
        yield call
        web.set_router(None, None)


def q_bytes(cfg, golden, tmp_path, name="q.xlsx", **kw):
    return write_questionnaire(tmp_path / name, cfg, golden["ratings"], kw.get("answers", {1: "scope text", 22: "There is no cloud team"})).read_bytes()


def make_run(host, cfg, golden, tmp_path, sid="t"):
    qid = host.upload(q_bytes(cfg, golden, tmp_path), "questionnaire", "q.xlsx")
    nid = host.upload(NOTE.encode(), "note", "cio.md")
    st, out, _ = host("POST", "/api/runs", {"upload_id": qid, "file_name": "q.xlsx", "set_id": sid, "customer": "Customer A",
                                           "notes": [{"name": "cio.md", "upload_id": nid}], "meta": {"facilitator": "Aivar"}})
    assert st == 202, out
    done = host.run_jobs()
    assert done and done[0]["state"] == "done"
    return out["job_id"]


# ---- who may do what ---------------------------------------------------------------------------------------------------------------------
def test_health_needs_no_sign_in_but_everything_else_does(host):
    assert host("GET", "/api/health", claims=None)[:2] == (200, {"ok": True, "version": host("GET", "/api/health", claims=None)[1]["version"]})
    assert host("GET", "/api/runs", claims=None)[0] == 401 and host("POST", "/api/uploads", {}, claims=None)[0] == 401


def test_a_reader_can_look_but_not_change_anything(host, cfg, golden, tmp_path):
    make_run(host, cfg, golden, tmp_path)
    assert host("GET", "/api/runs/t", claims=READER)[0] == 200 and host("GET", "/api/info", claims=READER)[1]["user"] == {"name": "rd@aivar.tech", "can_write": False}
    for path, body in (("/api/uploads", {"purpose": "note", "name": "a.txt"}), ("/api/runs/t/adjust", {"question": "Q42", "score": 1, "reason": "r"}),
                       ("/api/runs/t/export", {"kinds": ["xlsx"]}), ("/api/runs/t/finalise", {}), ("/api/runs", {})):
        st, out, _ = host("POST", path, body, claims=READER)
        assert st == 403 and "reviewer group" in out["error"], path
    host("POST", "/api/runs/t/export", {"kinds": ["xlsx"]})
    assert host("POST", "/api/runs/t/exports/MRA_t_DRAFT.xlsx/link", {}, claims=READER)[0] == 200     # reading a finished file is allowed


def test_the_caller_comes_from_the_token_never_from_a_header(host, cfg, golden, tmp_path):
    make_run(host, cfg, golden, tmp_path)
    host("POST", "/api/runs/t/decide", {"activity": "BC-1", "decision": "approve", "owner": "A", "due_date": "2026-12-15"},
         headers={"x-mra-actor": "mallory", "X-MRA-Actor": "mallory"})
    actors = {e["actor"] for e in host.svc.audit_entries("t")}
    assert "tc@aivar.tech" in actors and actors <= {"tc@aivar.tech", "agent"} and "mallory" not in actors
    info = host("GET", "/api/info")[1]
    assert info["hosted"] is True and info["user"] == {"name": "tc@aivar.tech", "can_write": True}


def test_username_is_used_when_there_is_no_email(host):
    claims = {"cognito:username": "demo", "cognito:groups": "[mra-reviewers]"}
    assert host("GET", "/api/info", claims=claims)[1]["user"]["name"] == "demo"


# ---- uploads ------------------------------------------------------------------------------------------------------------------------------------
def test_files_go_to_storage_through_a_presigned_form_and_are_removed_after_use(host, cfg, golden, tmp_path):
    st, out, _ = host("POST", "/api/uploads", {"purpose": "questionnaire", "name": "q.xlsx", "size": 1000})
    assert st == 200 and out["mode"] == "post" and out["limit"] == 25_000_000 and "key" in out["fields"] and out["fields"]["key"].startswith("uploads/")
    make_run(host, cfg, golden, tmp_path)
    assert [k for k in host.store.list("uploads/") if not k.endswith(".meta")] == []              # the files were consumed and deleted


def test_a_run_made_from_uploads_has_the_files_names_and_hashes(host, cfg, golden, tmp_path):
    make_run(host, cfg, golden, tmp_path)
    res = host.svc.result("t")
    assert set(res["meta"]["input_hashes"]) == {"q.xlsx", "notes/cio.md"} and host("GET", "/api/runs/t")[1]["blank_ratings"] == ["Q42"]
    assert [n["id"] for n in host("GET", "/api/runs/t")[1]["notes"]] == ["E-cio"]


@pytest.mark.parametrize("body,code,needle", [
    ({"purpose": "nope", "name": "x"}, 400, "purpose"), ({"purpose": "questionnaire", "name": "x.pdf"}, 400, "must be"),
    ({"purpose": "note", "name": "n.txt", "size": 5_000_000}, 413, "larger"), ({"purpose": "template", "name": "t.xlsx"}, 400, "must be")])
def test_upload_requests_are_checked(host, body, code, needle):
    st, out, _ = host("POST", "/api/uploads", body)
    assert st == code and needle in out["error"]


def test_a_file_bigger_than_its_limit_is_refused_even_if_storage_let_it_in(host, cfg, golden, tmp_path):
    """S3 enforces the size in the presigned form; this is the second line of defence (and what protects a fake that does not)."""
    st, out, _ = host("POST", "/api/uploads", {"purpose": "note", "name": "n.txt", "size": 100})
    host.store.put(f"uploads/{out['upload_id']}", b"x" * 2_500_000)
    qid = host.upload(q_bytes(cfg, golden, tmp_path), "questionnaire", "q.xlsx")
    st, body, _ = host("POST", "/api/runs", {"upload_id": qid, "file_name": "q.xlsx", "set_id": "t", "notes": [{"name": "n.txt", "upload_id": out["upload_id"]}]})
    assert st == 413 and "larger" in body["error"] and not host.store.exists(f"uploads/{out['upload_id']}")


def test_uploads_cannot_be_used_by_someone_else_or_for_the_wrong_thing_or_twice(host, cfg, golden, tmp_path):
    qid = host.upload(q_bytes(cfg, golden, tmp_path), "questionnaire", "q.xlsx")
    assert host("POST", "/api/runs", {"upload_id": qid, "file_name": "q.xlsx"}, claims=OTHER)[0] == 404           # another user's upload
    nid = host.upload(b"note", "note", "n.txt")
    assert host("POST", "/api/runs", {"upload_id": nid, "file_name": "n.txt"})[0] == 400                          # a note is not a questionnaire
    assert host("POST", "/api/runs", {"upload_id": "0" * 32, "file_name": "q.xlsx"})[0] == 404
    assert host("POST", "/api/runs", {"upload_id": "../x", "file_name": "q.xlsx"})[0] == 404
    assert host("POST", "/api/runs", {"upload_id": qid, "file_name": "q.xlsx", "set_id": "t"})[0] == 202            # the right user, the right use
    assert host("POST", "/api/runs", {"upload_id": qid, "file_name": "q.xlsx", "set_id": "t2"})[0] == 404           # an upload is used once


def test_an_upload_that_never_arrived_is_reported(host):
    st, out, _ = host("POST", "/api/uploads", {"purpose": "questionnaire", "name": "q.xlsx"})
    st, body, _ = host("POST", "/api/runs", {"upload_id": out["upload_id"], "file_name": "q.xlsx"})
    assert st == 400 and "not uploaded" in body["error"]


def test_questionnaire_problems_are_reported_at_once_not_in_a_job(host, cfg, golden, tmp_path):
    p = write_questionnaire(tmp_path / "bad.xlsx", cfg, golden["ratings"], overrides={26: {"Response": "3.5"}})
    qid = host.upload(p.read_bytes(), "questionnaire", "bad.xlsx")
    st, out, _ = host("POST", "/api/runs", {"upload_id": qid, "file_name": "bad.xlsx", "set_id": "t"})
    assert st == 422 and "must be a whole number" in out["errors"][0] and host.fake.sent == []


def test_small_files_may_still_be_sent_inline(host, cfg, golden, tmp_path):
    b = base64.b64encode(q_bytes(cfg, golden, tmp_path)).decode()
    assert host("POST", "/api/runs", {"file_b64": b, "file_name": "q.xlsx", "set_id": "t"})[0] == 202


# ---- jobs --------------------------------------------------------------------------------------------------------------------------------------------
def test_a_slow_route_is_queued_polled_and_finished(host, cfg, golden, tmp_path):
    qid = host.upload(q_bytes(cfg, golden, tmp_path), "questionnaire", "q.xlsx")
    st, out, _ = host("POST", "/api/runs", {"upload_id": qid, "file_name": "q.xlsx", "set_id": "t"})
    job = out["job_id"]
    assert st == 202 and host.fake.sent == [{"_mra_job": job}]
    assert host("GET", f"/api/jobs/{job}")[1] == {"state": "queued", "kind": "run"}
    assert host("GET", "/api/runs/t")[0] == 404                                                              # nothing exists until the job runs
    host.run_jobs()
    assert host("GET", f"/api/jobs/{job}")[1] == {"state": "done", "kind": "run", "result": {"set_id": "t"}}
    assert host("GET", "/api/runs/t")[0] == 200


def test_the_request_is_stored_not_sent_in_the_invocation_payload(host, cfg, golden, tmp_path):
    """An asynchronous Lambda invocation is limited to 256 KB: only the job id travels, the work is read back from S3."""
    qid = host.upload(q_bytes(cfg, golden, tmp_path), "questionnaire", "q.xlsx")
    host("POST", "/api/runs", {"upload_id": qid, "file_name": "q.xlsx", "set_id": "t"})
    assert list(host.fake.sent[0]) == ["_mra_job"] and len(json.dumps(host.fake.sent[0])) < 100
    assert any(k.startswith("jobs/") and k.endswith("request.json") for k in host.store.list("jobs/"))


def test_only_the_person_who_started_a_job_can_see_it(host, cfg, golden, tmp_path):
    job = make_run(host, cfg, golden, tmp_path)
    assert host("GET", f"/api/jobs/{job}", claims=OTHER)[0] == 404 and host("GET", f"/api/jobs/{job}", claims=READER)[0] == 404
    assert host("GET", "/api/jobs/" + "0" * 32)[0] == 404 and host("GET", "/api/jobs/not-a-job")[0] == 404


def test_a_second_slow_task_on_the_same_run_is_refused_until_the_first_ends(host, cfg, golden, tmp_path):
    make_run(host, cfg, golden, tmp_path)
    st, first, _ = host("POST", "/api/runs/t/adjust", {"question": "Q42", "score": 1, "reason": "r"})
    assert st == 202
    st, out, _ = host("POST", "/api/runs/t/agent", {"task": "context"})
    assert st == 409 and "still running" in out["error"]
    host.run_jobs()
    assert host("POST", "/api/runs/t/agent", {"task": "context"})[0] == 202


def test_a_job_that_died_does_not_block_the_run_for_ever(host, cfg, golden, tmp_path):
    make_run(host, cfg, golden, tmp_path)
    st, out, _ = host("POST", "/api/runs/t/agent", {"task": "context"})                             # queued, and the invocation never runs
    assert host("POST", "/api/runs/t/agent", {"task": "context"})[0] == 409
    host.call_clock = host.clock
    host.clock.t += 1300
    assert host("GET", f"/api/jobs/{out['job_id']}")[1]["state"] == "failed" and host("GET", f"/api/jobs/{out['job_id']}")[1]["status"] == 504
    assert host("POST", "/api/runs/t/agent", {"task": "context"})[0] == 202


def test_a_failed_job_carries_the_same_error_the_page_would_have_seen(host, cfg, golden, tmp_path):
    make_run(host, cfg, golden, tmp_path)
    st, out, _ = host("POST", "/api/runs/t/adjust", {"question": "Q9999", "score": 1, "reason": "r"})
    host.run_jobs()
    job = host("GET", f"/api/jobs/{out['job_id']}")[1]
    assert job["state"] == "failed" and job["status"] in (400, 404) and "error" in job["payload"]
    assert host("POST", "/api/runs/t/agent", {"task": "context"})[0] == 202                          # and the run is free again


def test_an_unexpected_crash_in_a_job_is_a_generic_error_and_frees_the_run(host, cfg, golden, tmp_path, monkeypatch):
    make_run(host, cfg, golden, tmp_path)
    monkeypatch.setattr(host.svc, "adjust", lambda *a, **k: 1 / 0)
    st, out, _ = host("POST", "/api/runs/t/adjust", {"question": "Q42", "score": 1, "reason": "r"})
    host.run_jobs()
    job = host("GET", f"/api/jobs/{out['job_id']}")[1]
    assert job["state"] == "failed" and job["status"] == 500 and "ZeroDivision" not in json.dumps(job)
    assert host("POST", "/api/runs/t/agent", {"task": "context"})[0] == 202


def test_if_the_background_task_cannot_be_started_the_run_is_not_left_locked(host, cfg, golden, tmp_path):
    make_run(host, cfg, golden, tmp_path)
    host.fake.fail = True
    st, out, _ = host("POST", "/api/runs/t/agent", {"task": "context"})
    assert st == 502 and "could not start" in out["error"]
    host.fake.fail = False
    assert host("POST", "/api/runs/t/agent", {"task": "context"})[0] == 202


def test_an_unknown_agent_task_or_run_is_refused_before_anything_is_queued(host, cfg, golden, tmp_path):
    make_run(host, cfg, golden, tmp_path)
    assert host("POST", "/api/runs/t/agent", {"task": "bogus"})[0] == 400 and host("POST", "/api/runs/nope/agent", {"task": "context"})[0] == 404
    assert host.fake.sent == []


def test_uploading_again_to_a_finalised_run_is_refused_at_once(host, cfg, golden, tmp_path):
    make_run(host, cfg, golden, tmp_path)
    host("POST", "/api/runs/t/adjust", {"question": "Q42", "score": 1, "reason": "r"})
    host.run_jobs()
    for a in host("GET", "/api/runs/t")[1]["activities"]:
        if a["needs_action"]:
            host("POST", "/api/runs/t/decide", {"activity": a["id"], "decision": "approve", "owner": "A", "due_date": "2026-12-15"})
    for i in host("GET", "/api/runs/t")[1]["validation"]:
        host("POST", "/api/runs/t/resolve", {"item": i["key"], "resolution": "accepted_risk", "note": "seen"})
    host("POST", "/api/runs/t/meta", {"facilitator": "F", "workshop_date": "2026-10-23", "readout_date": "2026-10-30"})
    assert host("POST", "/api/runs/t/confirm", {})[0] == 200 and host("POST", "/api/runs/t/finalise", {})[0] == 200
    qid = host.upload(q_bytes(cfg, golden, tmp_path, "q2.xlsx"), "questionnaire", "q2.xlsx")
    st, out, _ = host("POST", "/api/runs", {"upload_id": qid, "file_name": "q2.xlsx", "set_id": "t"})
    assert st == 409 and "finalised" in out["error"]


# ---- downloads -----------------------------------------------------------------------------------------------------------------------------------------
def test_a_finished_file_is_downloaded_straight_from_storage_through_a_short_lived_link(host, cfg, golden, tmp_path):
    make_run(host, cfg, golden, tmp_path)
    files = host("POST", "/api/runs/t/export", {"kinds": ["xlsx", "docx"]})[1]["files"]
    assert sorted(f["name"] for f in files) == ["MRA_t_DRAFT.docx", "MRA_t_DRAFT.xlsx"]
    st, link, _ = host("POST", "/api/runs/t/exports/MRA_t_DRAFT.xlsx/link", {})
    assert st == 200 and link["needs_token"] is False and "X-Amz-Signature" in link["url"] and "X-Amz-Expires=300" in link["url"]
    r = requests.get(link["url"])
    assert r.status_code == 200 and r.content[:2] == b"PK" and 'attachment; filename="MRA_t_DRAFT.xlsx"' in r.headers["Content-Disposition"]
    assert host("GET", "/api/runs/t/exports/MRA_t_DRAFT.xlsx")[0] == 404                         # the API never carries the file itself (Lambda's 6 MB limit)


@pytest.mark.parametrize("name", ["../result.json", "result.json", "MRA_t_DRAFT.exe", "MRA_nope_DRAFT.xlsx"])
def test_download_links_only_for_the_runs_own_exports(host, cfg, golden, tmp_path, name):
    make_run(host, cfg, golden, tmp_path)
    assert host("POST", f"/api/runs/t/exports/{name}/link", {})[0] == 404


def test_the_deck_is_built_within_one_request_and_used_from_the_built_in_default(host, cfg, golden, tmp_path):
    make_run(host, cfg, golden, tmp_path)
    st, out, _ = host("POST", "/api/runs/t/export", {"kinds": ["pptx"]})
    assert st == 200 and out["deck_template"] == "Ingram MAP deliverable (built in)" and sorted(f["name"] for f in out["files"]) == ["MRA_t_DRAFT.pptx", "MRA_t_DRAFT_open_items.md"]
    assert [f for f in out["files"] if f["name"].endswith(".pptx")][0]["size"] > 1_000_000


def test_a_large_deck_template_is_uploaded_directly_and_checked(host):
    from mra.outputs.deck_template import default_template_bytes
    data = default_template_bytes()
    uid = host.upload(data, "template", "my deck.pptx")                                                  # 6 MB: far too big for the API itself
    st, out, _ = host("POST", "/api/templates", {"kind": "pptx", "file_name": "my deck.pptx", "upload_id": uid, "prefer": True})
    assert st == 200 and out["coverage"]["complete"] and host("GET", "/api/templates")[1]["preferred"] == out["id"]


def test_previews_are_not_offered_when_hosted(host, cfg, golden, tmp_path):
    make_run(host, cfg, golden, tmp_path)
    assert host("GET", "/api/info")[1]["libreoffice"] is False                      # even on a machine that has LibreOffice
    assert host("GET", "/api/runs/t/preview/docx.pdf")[0] == 404
    st, out, _ = host("POST", "/api/runs/t/preview", {"kind": "docx"})
    assert st == 501 and "hosted version" in out["error"]


# ---- the request itself ---------------------------------------------------------------------------------------------------------------------------------
def test_bad_requests_get_clear_errors_and_base64_bodies_are_understood(host):
    assert host("POST", "/api/runs", raw_body="not json")[0] == 400 and host("POST", "/api/runs", raw_body="[1]")[0] == 400
    assert host("POST", "/api/runs", raw_body="x" * 7_000_000)[0] == 413
    assert host("POST", "/api/uploads", {"purpose": "note", "name": "n.txt"}, b64=True)[0] == 200
    assert host("GET", "/api/nothing")[0] == 404 and host("DELETE", "/api/runs/t")[0] in (404, 405)


def test_an_unexpected_error_reveals_nothing_internal(host, monkeypatch):
    monkeypatch.setattr(host.svc, "result", lambda sid: 1 / 0)
    st, out, _ = host("GET", "/api/runs/t")
    assert st == 500 and "ZeroDivision" not in json.dumps(out) and "Traceback" not in json.dumps(out)


def test_every_response_is_uncached_json(host):
    st, out, headers = host("GET", "/api/runs")
    assert headers["Content-Type"].startswith("application/json") and headers["Cache-Control"] == "no-store" and headers["X-Content-Type-Options"] == "nosniff"
