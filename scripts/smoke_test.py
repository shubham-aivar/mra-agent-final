"""Check a deployed MRA agent end to end, the way a person would use it, with synthetic data only (standard library only; runs anywhere).

    python3 scripts/smoke_test.py --url https://dxxxxxxxx.cloudfront.net --username mra-analyst        # asks for the password
    MRA_SMOKE_PASSWORD=... python3 scripts/smoke_test.py --url ... --username mra-analyst --skip-agent

It signs in, uploads a synthetic questionnaire and notes straight to storage, scores them, runs an agent task, changes a rating, creates the
Excel, Word and PowerPoint files and downloads each through its signed link, then deletes the run. Each line is PASS or FAIL; the exit code is
0 only if all pass.
With the real model this uses a few thousand tokens. Do not point it at real customer data.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def http(method: str, url: str, headers: dict | None = None, body: bytes | None = None, timeout: int = 90) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


class Run:
    def __init__(self, a: argparse.Namespace):
        self.a, self.base, self.results, self.token, self.sid = a, a.url.rstrip("/"), [], None, None
        self.cfg: dict = {}

    # ---- helpers --------------------------------------------------------------------------------------------------------------
    def api(self, method: str, path: str, obj: dict | None = None, token: bool = True) -> tuple[int, dict]:
        h = {"Content-Type": "application/json"}
        if token and self.token:
            h["Authorization"] = "Bearer " + self.token
        st, _, body = http(method, self.base + path, h, json.dumps(obj).encode() if obj is not None else None)
        try:
            return st, json.loads(body or b"{}")
        except json.JSONDecodeError:
            return st, {"raw": body[:200].decode("utf-8", "replace")}

    def job(self, method: str, path: str, obj: dict) -> dict:
        st, out = self.api(method, path, obj)
        if st != 202:
            raise AssertionError(f"expected 202 (queued), got {st}: {out}")
        t0 = time.time()
        while time.time() - t0 < self.a.timeout_minutes * 60:
            time.sleep(2)
            st, j = self.api("GET", "/api/jobs/" + out["job_id"])
            if st != 200:
                raise AssertionError(f"job status {st}: {j}")
            if j["state"] == "done":
                j["seconds"] = round(time.time() - t0, 1)
                return j["result"] | {"_seconds": j["seconds"]}
            if j["state"] == "failed":
                raise AssertionError(f"the task failed ({j.get('status')}): {j.get('payload')}")
        raise AssertionError("the task did not finish in time")

    def upload(self, data: bytes, purpose: str, name: str) -> str:
        st, s = self.api("POST", "/api/uploads", {"purpose": purpose, "name": name, "size": len(data)})
        if st != 200 or s.get("mode") != "post":
            raise AssertionError(f"upload session: {st} {s}")
        boundary = uuid.uuid4().hex
        head = f'--{boundary}\r\nContent-Disposition: form-data; name='
        parts = b"".join(f'{head}"{k}"\r\n\r\n{v}\r\n'.encode() for k, v in s["fields"].items())
        parts += f'{head}"file"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
        parts += data + f"\r\n--{boundary}--\r\n".encode()
        st, _, body = http("POST", s["url"], {"Content-Type": "multipart/form-data; boundary=" + boundary}, parts)
        if st not in (200, 201, 204):
            raise AssertionError(f"storage refused the upload: {st} {body[:200]!r}")
        return s["upload_id"]

    def step(self, name: str, fn) -> None:
        t0 = time.time()
        try:
            detail = fn() or ""
            self.results.append((True, name))
            print(f"PASS  {name}" + (f"  ({detail})" if detail else "") + f"  [{time.time() - t0:.1f}s]")
        except Exception as e:
            self.results.append((False, name))
            print(f"FAIL  {name}\n      {type(e).__name__}: {e}")

    # ---- the checks ---------------------------------------------------------------------------------------------------------------
    def check_page(self):
        st, h, body = http("GET", self.base + "/")
        assert st == 200 and b"A04 MRA Agent" in body, f"page answered {st}"
        csp = h.get("content-security-policy", "")
        assert "script-src 'self'" in csp and "unsafe-inline" not in csp, "no strict content policy on the page"
        protected = "strict-transport-security" in h and h.get("x-content-type-options") == "nosniff" and h.get("x-frame-options") == "DENY"
        assert protected, "missing browser protections"
        st, _, _ = http("GET", self.base + "/static/app.js")
        assert st == 200, f"the page's script answered {st}"
        return "page, script and security headers"

    def check_config(self):
        st, _, body = http("GET", self.base + "/config.json")
        self.cfg = json.loads(body)
        ready = self.cfg.get("hosted") and self.cfg.get("userPoolId") and self.cfg.get("clientId")
        assert st == 200 and ready, "config.json is not the hosted settings"
        return self.cfg["userPoolId"]

    def check_health(self):
        st, out = self.api("GET", "/api/health", token=False)
        assert st == 200 and out.get("ok"), f"health answered {st}"
        return "version " + out.get("version", "?")

    def check_locked(self):
        st, _ = self.api("GET", "/api/runs", token=False)
        assert st == 401, f"the API answered {st} without a token (should be 401)"

    def sign_in(self):
        pw = os.environ.get("MRA_SMOKE_PASSWORD") or getpass.getpass(f"Password for {self.a.username} (hidden): ")
        url = self.cfg.get("cognitoEndpoint") or f"https://cognito-idp.{self.cfg['region']}.amazonaws.com/"
        target = "AWSCognitoIdentityProviderService.InitiateAuth"
        st, _, body = http("POST", url, {"Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": target},
                           json.dumps({"AuthFlow": "USER_PASSWORD_AUTH", "ClientId": self.cfg["clientId"],
                                       "AuthParameters": {"USERNAME": self.a.username, "PASSWORD": pw}}).encode())
        out = json.loads(body)
        assert st == 200, f"sign-in refused: {out.get('message') or out.get('__type')}"
        step = out.get("ChallengeName")
        assert "AuthenticationResult" in out, (f"this account needs a step this script cannot do ({step}): sign in once in the page and "
                                               "choose a new password, or create the user with --permanent")
        self.token = out["AuthenticationResult"]["IdToken"]

    def check_info(self):
        st, i = self.api("GET", "/api/info")
        assert st == 200 and i["hosted"] and i["storage"] == "aws", f"info answered {st}: hosted={i.get('hosted')}, storage={i.get('storage')}"
        assert i["user"]["can_write"], "this user cannot change things: use a user made without --reader"
        self.engine = i["engine"]
        if self.a.expect_engine:
            assert self.engine == self.a.expect_engine, f"the text engine is '{self.engine}', expected '{self.a.expect_engine}'"
        return f"engine {self.engine}, signed in as {i['user']['name']}"

    def make_run(self):
        from scripts.make_sample_questionnaire import build
        self.sid = "smoke-" + time.strftime("%m%d-%H%M%S")
        tmp = Path(tempfile.mkdtemp())
        q = build(tmp / "smoke questionnaire.xlsx")
        files = sorted((ROOT / "samples" / "notes").glob("*.md"))
        notes = [{"name": p.name, "upload_id": self.upload(p.read_bytes(), "note", p.name)} for p in files]
        qid = self.upload(q.read_bytes(), "questionnaire", q.name)
        meta = {"facilitator": "smoke test", "workshop_date": "2026-11-02", "readout_date": "2026-11-09"}
        r = self.job("POST", "/api/runs", {"upload_id": qid, "file_name": q.name, "set_id": self.sid, "customer": "Smoke Test",
                                           "notes": notes, "meta": meta})
        assert r["set_id"] == self.sid
        return f"{len(notes)} notes, scored in {r['_seconds']}s"

    def check_run(self):
        st, v = self.api("GET", f"/api/runs/{self.sid}")
        assert st == 200 and v["customer"] == "Smoke Test" and v["blank_ratings"] == ["Q42"], f"unexpected run: {st}"
        assert any(p["status"] == "incomplete" for p in v["perspectives"]), "a blank rating should leave a perspective Incomplete"
        return f"engine {v['engine']}, {len(v['activities'])} activities"

    def agent(self):
        r = self.job("POST", f"/api/runs/{self.sid}/agent", {"task": "context"})
        assert r["stop_reason"] in ("done", "max_steps"), f"the agent stopped because {r['stop_reason']}"
        return f"stopped: {r['stop_reason']}, {r['tool_calls']} tool calls, {len(r['proposal_ids'])} proposals, {r['_seconds']}s"

    def adjust(self):
        self.job("POST", f"/api/runs/{self.sid}/adjust", {"question": "Q42", "score": 1, "reason": "smoke test"})
        st, v = self.api("GET", f"/api/runs/{self.sid}")
        assert v["blank_ratings"] == [], "the blank rating was not cleared"

    def exports(self):
        st, out = self.api("POST", f"/api/runs/{self.sid}/export", {"kinds": ["xlsx", "docx", "pptx"]})
        assert st == 200, f"export: {st} {out}"
        sizes = []
        for f in out["files"]:
            st, link = self.api("POST", f["url"] + "/link", {})
            assert st == 200 and "X-Amz-Signature" in link["url"], f"no signed link for {f['name']}: {st}"
            st, _, data = http("GET", link["url"])                                  # no Authorization header: the link is the credential
            assert st == 200 and len(data) > 500, f"{f['name']}: storage answered {st}"
            if f["name"].endswith((".xlsx", ".docx", ".pptx")):
                assert data[:2] == b"PK", f"{f['name']} is not an Office file"
            sizes.append(f"{f['name'].rsplit('_', 1)[-1]} {len(data) // 1024} KB")
        return ", ".join(sizes)

    def cleanup(self):
        if self.sid and not self.a.keep:
            st, _ = self.api("POST", f"/api/runs/{self.sid}/purge", {"confirm": self.sid})
            assert st == 200, f"could not delete the smoke run ({st})"
            assert self.api("GET", f"/api/runs/{self.sid}")[0] == 404, "the run is still there"

    def go(self) -> int:
        for name, fn in (("the page is served with its security headers", self.check_page), ("the hosted settings are published", self.check_config),
                         ("the API's health check answers", self.check_health), ("the API refuses a request with no token", self.check_locked),
                         ("sign in with a username and password", self.sign_in), ("the signed-in user may change things", self.check_info),
                         ("upload to storage, then score as a background job", self.make_run),
                         ("the run is readable and a blank rating is Incomplete", self.check_run)):
            self.step(name, fn)
            if not self.results[-1][0] and name.startswith(("the page", "the hosted", "sign in", "the signed-in", "upload")):
                break                                                           # nothing later can work
        else:
            if not self.a.skip_agent:
                self.step("an agent task runs as a background job", self.agent)
            self.step("changing a rating runs as a background job", self.adjust)
            self.step("Excel, Word and PowerPoint are created and downloaded through signed links", self.exports)
        if self.sid:
            self.step("the smoke run is deleted", self.cleanup)
        failed = sum(1 for ok, _ in self.results if not ok)
        print(f"\n{len(self.results) - failed} passed, {failed} failed")
        return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="the SiteUrl printed by the deployment")
    ap.add_argument("--username", required=True)
    ap.add_argument("--skip-agent", action="store_true", help="do not run the agent task")
    ap.add_argument("--expect-engine", default="bedrock", help="the text engine the deployment should report (use '' to skip)")
    ap.add_argument("--timeout-minutes", type=float, default=8)
    ap.add_argument("--keep", action="store_true", help="do not delete the smoke run afterwards")
    return Run(ap.parse_args(argv)).go()


if __name__ == "__main__":
    sys.exit(main())
