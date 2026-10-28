"""Background jobs for the hosted page. API Gateway answers a request within 30 seconds, but scoring or an agent task with the real model
can take minutes. So those routes are queued: the request is validated, stored, and the Lambda function invokes itself (asynchronously) to do
the work. The page polls the job. State is in the same storage as the runs (S3), so it survives separate invocations.

One slow task per run at a time: a second one is refused with 409 (a queued or running job that is older than `stale` seconds is treated as dead).
"""
from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable

from .api import UiError

STALE_SECONDS = 1200                   # Lambda stops a function after 900 seconds


class JobQueue:
    def __init__(self, storage, invoker: Callable[[dict], None], clock: Callable[[], float] = time.time, stale: int = STALE_SECONDS):
        self.s, self.invoke, self.clock, self.stale = storage, invoker, clock, stale

    # ---- storage keys ----------------------------------------------------------------------------------------------
    @staticmethod
    def _k(job_id: str, name: str) -> str:
        return f"jobs/{job_id}/{name}.json"

    def _read(self, key: str) -> dict | None:
        return json.loads(self.s.get(key)) if self.s.exists(key) else None

    def _write(self, key: str, obj: dict) -> None:
        self.s.put(key, json.dumps(obj, sort_keys=True).encode())

    def _busy(self, sid: str) -> bool:
        lock = self._read(f"runs/{sid}/job.json")
        return bool(lock and lock["state"] in ("queued", "running") and self.clock() - lock["at"] < self.stale)

    # ---- the request side --------------------------------------------------------------------------------------------
    def submit(self, kind: str, sid: str, actor: str, work: dict) -> str:
        if self._busy(sid):
            raise UiError(409, {"error": "another task is still running for this run: wait for it to finish, then try again"})
        job_id = secrets.token_hex(16)
        now = self.clock()
        self._write(self._k(job_id, "request"), {"kind": kind, "sid": sid, "actor": actor, "work": work})
        self._write(self._k(job_id, "status"), {"state": "queued", "kind": kind, "sid": sid, "actor": actor, "at": now})
        self._write(f"runs/{sid}/job.json", {"job_id": job_id, "state": "queued", "at": now})
        try:
            self.invoke({"_mra_job": job_id})
        except Exception as e:                                         # could not start: do not leave a lock behind
            failed = {"state": "failed", "status": 502, "payload": {"error": "could not start the background task; try again"}}
            self._finish(job_id, sid, failed, actor, kind)
            raise UiError(502, {"error": "could not start the background task; try again"}) from e
        return job_id

    def status(self, job_id: str, actor: str) -> dict:
        st = self._read(self._k(job_id, "status")) if len(job_id) == 32 and job_id.isalnum() else None
        if st is None or st["actor"] != actor:
            raise UiError(404, {"error": "no such task"})
        if st["state"] in ("queued", "running") and self.clock() - st["at"] > self.stale:
            return {"state": "failed", "kind": st["kind"], "status": 504,
                    "payload": {"error": "the task did not finish (it may have timed out): try again"}}
        out = {"state": st["state"], "kind": st["kind"]}
        for k in ("result", "status", "payload"):
            if k in st:
                out[k] = st[k]
        return out

    # ---- the worker side (runs in the asynchronous invocation) -----------------------------------------------------------
    def work(self, job_id: str, run_job: Callable[[str, dict, str], dict], map_error: Callable[[BaseException], tuple[int, dict]]) -> dict:
        req = self._read(self._k(job_id, "request"))
        if req is None:
            return {"state": "failed", "status": 404, "payload": {"error": "no such task"}}
        kind, sid, actor = req["kind"], req["sid"], req["actor"]
        started = self.clock()
        self._write(self._k(job_id, "status"), {"state": "running", "kind": kind, "sid": sid, "actor": actor, "at": started})
        self._write(f"runs/{sid}/job.json", {"job_id": job_id, "state": "running", "at": started})
        try:
            outcome = {"state": "done", "result": run_job(kind, req["work"], actor)}
        except Exception as e:
            status, payload = map_error(e)
            outcome = {"state": "failed", "status": status, "payload": payload}
        return self._finish(job_id, sid, outcome, actor, kind)

    def _finish(self, job_id: str, sid: str, outcome: dict, actor: str, kind: str) -> dict:
        now = self.clock()
        self._write(self._k(job_id, "status"), {"kind": kind, "sid": sid, "actor": actor, "at": now, **outcome})
        if self._read(f"runs/{sid}/job.json") and self._read(f"runs/{sid}/job.json")["job_id"] == job_id:
            self._write(f"runs/{sid}/job.json", {"job_id": job_id, "state": outcome["state"], "at": now})
        return outcome
