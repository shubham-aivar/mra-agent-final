"""AWS Lambda entry point for the hosted page's API: CloudFront -> API Gateway HTTP API (Cognito JWT authorizer) -> this function.

Two kinds of invocation:
* an HTTP request (API Gateway payload format 2.0): quick routes answer directly; slow ones (scoring, agent tasks, rating changes) are queued as
  jobs and answer 202 with a job id, because API Gateway gives a request 30 seconds;
* a job ({"_mra_job": id}) that this function sends itself with InvocationType=Event: it does the slow work and records the result in S3.

Sign-in is done by API Gateway (the authorizer verifies the Cognito token); this code reads the verified claims. Anything that changes data needs
the Cognito group named in MRA_REVIEWER_GROUP. The caller's identity always comes from the token, never from a header the page could set.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path

from mra import __version__
from mra.adapters.factory import build_llm, build_storage
from mra.loader import load_config
from mra.service import AssessmentService
from mra.settings import load_settings
from mra.ui.api import UiApi
from mra.ui.jobs import JobQueue
from mra.ui.routes import Caller, Resp, Router, map_error

log = logging.getLogger()
log.setLevel(logging.INFO)
MAX_BODY = 6_000_000                       # Lambda's own limit for a request payload
_ROUTER: Router | None = None
_LAMBDA = None


def _lambda_client():
    global _LAMBDA
    if _LAMBDA is None:
        import boto3
        _LAMBDA = boto3.client("lambda")
    return _LAMBDA


def _invoke_self(payload: dict) -> None:
    _lambda_client().invoke(FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"], InvocationType="Event", Payload=json.dumps(payload).encode())


def get_router() -> Router:
    global _ROUTER
    if _ROUTER is None:
        cfg_dir = os.environ.get("MRA_CONFIG_DIR", str(Path(__file__).resolve().parent.parent / "config"))
        s = load_settings(Path(cfg_dir) / "settings.json")
        svc = AssessmentService(build_storage(s), build_llm(s), load_config(cfg_dir), s)
        _ROUTER = Router(UiApi(svc, hosted=True), JobQueue(svc.s, _invoke_self), hosted=True)
    return _ROUTER


def set_router(router: Router | None, lambda_client=None) -> None:
    """For tests: inject a router (and a fake Lambda client)."""
    global _ROUTER, _LAMBDA
    _ROUTER, _LAMBDA = router, lambda_client


# ---- who is calling -------------------------------------------------------------------------------------------------------------------
def _groups(claims: dict) -> set[str]:
    raw = claims.get("cognito:groups", [])
    if isinstance(raw, str):                                   # API Gateway renders a list claim as "[a b]"
        raw = re.split(r"[,\s]+", raw.strip("[] "))
    return {g for g in raw if g}


def _caller(event: dict) -> Caller | None:
    claims = (event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}) or {}).get("claims") or {}
    if not claims:
        return None
    actor = claims.get("email") or claims.get("cognito:username") or claims.get("username") or claims.get("sub") or "unknown"
    return Caller(str(actor)[:80], os.environ.get("MRA_REVIEWER_GROUP", "mra-reviewers") in _groups(claims))


# ---- the two kinds of invocation ----------------------------------------------------------------------------------------------------
def _reply(resp: Resp) -> dict:
    headers = {"Content-Type": resp.ctype, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", **resp.headers}
    return {"statusCode": resp.status, "headers": headers, "body": resp.body.decode("utf-8")}


def _http(event: dict) -> dict:
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    if path == "/api/health":
        return _reply(Resp.json(200, {"ok": True, "version": __version__}))
    who = _caller(event)
    if who is None:
        return _reply(Resp.json(401, {"error": "not signed in"}))
    body: dict = {}
    if method == "POST":
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8", "replace")
        if len(raw) > MAX_BODY:
            return _reply(Resp.json(413, {"error": "the request is too large: upload files with the upload step"}))
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return _reply(Resp.json(400, {"error": "the request body is not valid JSON"}))
        if not isinstance(body, dict):
            return _reply(Resp.json(400, {"error": "the request body must be a JSON object"}))
    resp = get_router().handle(method, path, body, who)
    log.info("%s %s -> %s by %s", method, path, resp.status, who.actor)
    return _reply(resp)


def _job(job_id: str) -> dict:
    router = get_router()
    out = router.jobs.work(job_id, router.run_job, map_error)
    log.info("job %s -> %s", job_id, out.get("state"))
    return {"job": job_id, "state": out.get("state")}


def handler(event: dict, context=None) -> dict:
    if "_mra_job" in event:
        return _job(str(event["_mra_job"]))
    try:
        return _http(event)
    except Exception:
        log.exception("unhandled error")
        return _reply(Resp.json(500, {"error": "internal error"}))
