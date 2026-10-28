"""One dispatcher for the page's API, used by the local server and by the hosted Lambda function. It never reads a socket or an API Gateway
event: callers pass in the method, path, query, JSON body and who is calling, and get back a Resp.

Slow routes (scoring, agent tasks, rating changes) call the model. With a JobQueue they are queued and answer 202 with a job id; without one
(the local page) they run inline and answer 200 with the result, so the page's code is the same either way.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from ..loader import InputValidationError
from ..review.workflow import ReleaseBlocked, ReviewLocked
from .api import UiApi, UiError
from .jobs import JobQueue

log = logging.getLogger("mra.ui")
RID = r"(?P<id>[A-Za-z0-9][A-Za-z0-9_.-]{0,63})"
READ_ONLY_POSTS = re.compile(r"^/api/runs/[^/]+/exports/[^/]+/link$")
SLOW_ACTIONS = ("agent", "adjust", "proposals")
NO_WRITE = {"error": "this account can read but not change anything: ask for the reviewer group"}


@dataclass
class Resp:
    status: int
    body: bytes
    ctype: str = "application/json; charset=utf-8"
    headers: dict = field(default_factory=dict)

    @staticmethod
    def json(status: int, payload: dict) -> Resp:
        return Resp(status, json.dumps(payload).encode("utf-8"))


@dataclass
class Caller:
    actor: str
    can_write: bool = True


def map_error(e: BaseException) -> tuple[int, dict]:
    """Turn an exception into an HTTP status and a JSON body the page understands."""
    if isinstance(e, UiError):
        return e.status, e.payload
    if isinstance(e, InputValidationError):
        return 422, {"errors": e.errors}
    if isinstance(e, ReleaseBlocked):
        return 409, {"error": "cannot finalise yet", "blockers": e.blockers}
    if isinstance(e, ReviewLocked):
        return 409, {"error": str(e)}
    if isinstance(e, KeyError):
        return 404, {"error": f"not found: {e.args[0] if e.args else ''}"}
    if isinstance(e, ValueError):
        return 400, {"error": str(e)}
    log.error("unhandled error", exc_info=e)
    return 500, {"error": "internal error (details are in the log)"}


class Router:
    def __init__(self, api: UiApi, jobs: JobQueue | None = None, hosted: bool = False):
        self.api, self.jobs, self.hosted = api, jobs, hosted

    # ---- entry point -------------------------------------------------------------------------------------------------------
    def handle(self, method: str, path: str, body: dict, who: Caller) -> Resp:
        if method == "POST" and not who.can_write and not READ_ONLY_POSTS.match(path):
            return Resp.json(403, NO_WRITE)
        try:
            return self._dispatch(method, path, body, who)
        except (ConnectionError, TimeoutError):
            raise
        except Exception as e:
            status, payload = map_error(e)
            return Resp.json(status, payload)

    def run_job(self, kind: str, work: dict, actor: str) -> dict:
        """The slow part of a queued route. Also what an inline (local) request runs."""
        api = self.api
        if kind == "run":
            return api.execute_run(work, actor)
        if kind == "agent":
            return api.agent(work["sid"], work["body"], actor)
        if kind == "adjust":
            api.adjust(work["sid"], work["body"], actor)
            return {"ok": True}
        if kind == "proposals":
            return api.decide_proposals(work["sid"], work["body"], actor)
        raise ValueError(f"unknown task '{kind}'")

    def _slow(self, kind: str, sid: str, work: dict, who: Caller) -> Resp:
        if self.jobs is None:
            return Resp.json(200, self.run_job(kind, work, who.actor))
        return Resp.json(202, {"job_id": self.jobs.submit(kind, sid, who.actor, work)})

    # ---- routes --------------------------------------------------------------------------------------------------------------
    def _dispatch(self, method: str, path: str, body: dict, who: Caller) -> Resp:
        api = self.api
        if method == "GET":
            if path == "/api/info":
                return Resp.json(200, {**api.info(), "user": {"name": who.actor, "can_write": who.can_write}})
            if path == "/api/runs":
                return Resp.json(200, api.list_runs())
            if path == "/api/templates":
                return Resp.json(200, api.list_templates())
            if m := re.fullmatch(r"/api/jobs/(?P<job>[0-9a-f]{32})", path):
                if self.jobs is None:
                    return Resp.json(404, {"error": "no such task"})
                return Resp.json(200, self.jobs.status(m["job"], who.actor))
            if m := re.fullmatch(rf"/api/runs/{RID}", path):
                return Resp.json(200, api.view(m["id"]))
            if not self.hosted:                                                   # large files go straight from S3 when hosted
                if m := re.fullmatch(rf"/api/runs/{RID}/exports/([^/]+)", path):
                    data, ctype = api.download(m["id"], m.group(2))
                    return Resp(200, data, ctype, {"Content-Disposition": f'attachment; filename="{m.group(2)}"'})
                if m := re.fullmatch(rf"/api/runs/{RID}/preview/(pptx|docx)\.pdf", path):
                    return Resp(200, api.preview_pdf(m["id"], m.group(2)), "application/pdf", {"Content-Disposition": "inline"})
            return Resp.json(404, {"error": "not found"})
        if method != "POST":
            return Resp.json(405, {"error": "method not allowed"})
        if path == "/api/uploads":
            return Resp.json(200, api.start_upload(body, who.actor))
        if path == "/api/runs":
            prep = api.prepare_run(body, who.actor)                              # quick: every problem with the files is reported now
            return self._slow("run", prep["raw"]["set_id"], prep, who)
        if path == "/api/templates":
            return Resp.json(200, api.upload_template(body, who.actor))
        if m := re.fullmatch(r"/api/templates/(?P<tid>[0-9a-f]{10}|ingram|aws)/(prefer|delete)", path):
            return Resp.json(200, api.prefer_template(m["tid"]) if m.group(2) == "prefer" else api.delete_template(m["tid"]))
        if m := re.fullmatch(rf"/api/runs/{RID}/exports/(?P<name>[^/]+)/link", path):
            return Resp.json(200, api.download_link(m["id"], m["name"]))
        m = re.fullmatch(rf"/api/runs/{RID}/(adjust|decide|resolve|meta|confirm|finalise|reopen|agent|proposals|export|preview|purge)", path)
        if not m:
            return Resp.json(404, {"error": "not found"})
        sid, action = m["id"], m.group(2)
        if action in SLOW_ACTIONS:
            if action == "agent":
                api.check_agent(sid, body)
            return self._slow("agent" if action == "agent" else action, sid, {"sid": sid, "body": body}, who)
        simple = {"decide": api.decide, "resolve": api.resolve, "meta": api.meta, "reopen": api.reopen}
        if action in simple:
            simple[action](sid, body, who.actor)
            return Resp.json(200, {"ok": True})
        if action in ("confirm", "finalise"):
            getattr(api, action)(sid, who.actor)
            return Resp.json(200, {"ok": True})
        if action == "export":
            return Resp.json(200, api.export(sid, body, who.actor))
        if action == "preview":
            return Resp.json(200, api.preview(sid, body, who.actor))
        return Resp.json(200, api.purge(sid, body, who.actor))

