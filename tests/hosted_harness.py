"""A stand-in for the deployed AWS path, so the hosted page can be tested in a real browser without an AWS account:

    browser --> "CloudFront" (this file: serves the page with the production security headers; sends /api/* on)
                  --> "API Gateway" (this file: only GET/POST under /api, checks a Cognito token, builds the event) --> hosted/web.py, the real handler
    Cognito and S3 are moto servers (real HTTP, real sign-in challenges, presigned uploads and downloads, CORS).
    A function invoked "asynchronously" runs the real handler in a thread.

What this does not prove: IAM, CloudFront behaviours, real API Gateway or Cognito, Bedrock. The CDK tests and scripts/smoke_test.py cover those.
The token check here reads the claims but does not verify the signature (API Gateway does that for real).
"""
from __future__ import annotations

import base64
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import boto3
from botocore.config import Config
from moto.server import ThreadedMotoServer

from hosted import web
from mra.adapters.aws_storage import AwsStorage
from mra.adapters.llm_stub import StubLlm
from mra.service import AssessmentService
from mra.settings import Settings
from mra.ui.api import UiApi
from mra.ui.csp import hosted_csp
from mra.ui.jobs import JobQueue
from mra.ui.routes import Router
from scripts.build_site import build as build_site

REGION, BUCKET, GROUP = "us-east-1", "mra-harness-data", "mra-reviewers"
USERS = {"demo": ("Demo#Pass12345", True), "reader": ("Reader#Pass12345", False)}          # name -> (password, in the reviewer group)
FRESH = ("fresh", "Temp#12345")                                                              # created with a temporary password
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AsyncLambda:
    """An asynchronous invocation: the real handler runs in its own thread shortly after the caller has been answered."""

    def invoke(self, **kw):
        payload = json.loads(kw["Payload"])
        threading.Thread(target=lambda: (time.sleep(0.15), web.handler(payload)), daemon=True).start()


class Harness:
    def __init__(self, cfg: dict, root: Path):
        self.cfg, self.root = cfg, Path(root)
        self.reject_next = False
        self.log: list[tuple[str, str, int]] = []                                               # (method, path, status) of what the edge handled
        self.moto_port = free_port()
        self.moto = ThreadedMotoServer(port=self.moto_port, verbose=False)

    # ---- start / stop -------------------------------------------------------------------------------------------------------------
    def start(self) -> Harness:
        self.moto.start()
        time.sleep(0.5)
        ep = f"http://127.0.0.1:{self.moto_port}"
        self.endpoint = ep
        kw = {"endpoint_url": ep, "region_name": REGION, "aws_access_key_id": "x", "aws_secret_access_key": "y"}
        self.s3 = boto3.client("s3", **kw, config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
        self.s3.create_bucket(Bucket=BUCKET)
        self.s3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration={"CORSRules": [{                  # the same rule the stack sets
            "AllowedOrigins": ["*"], "AllowedMethods": ["GET", "POST", "PUT", "HEAD"], "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3000}]})
        self.idp = boto3.client("cognito-idp", **kw)
        self.pool = self.idp.create_user_pool(PoolName="mra", Policies={"PasswordPolicy": {"MinimumLength": 10}})["UserPool"]["Id"]
        self.client = self.idp.create_user_pool_client(UserPoolId=self.pool, ClientName="web", ExplicitAuthFlows=[
            "ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"])["UserPoolClient"]["ClientId"]
        self.idp.create_group(GroupName=GROUP, UserPoolId=self.pool)
        for name, (password, reviewer) in USERS.items():
            self.idp.admin_create_user(UserPoolId=self.pool, Username=name, TemporaryPassword="Temp#12345", MessageAction="SUPPRESS")
            self.idp.admin_set_user_password(UserPoolId=self.pool, Username=name, Password=password, Permanent=True)
            if reviewer:
                self.idp.admin_add_user_to_group(UserPoolId=self.pool, Username=name, GroupName=GROUP)
        self.idp.admin_create_user(UserPoolId=self.pool, Username=FRESH[0], TemporaryPassword=FRESH[1], MessageAction="SUPPRESS")
        self.idp.admin_add_user_to_group(UserPoolId=self.pool, Username=FRESH[0], GroupName=GROUP)

        storage = AwsStorage(BUCKET, REGION, client=self.s3)
        self.svc = AssessmentService(storage, StubLlm(), self.cfg, Settings(storage_mode="aws", bucket=BUCKET, region=REGION))
        fake = AsyncLambda()
        router = Router(UiApi(self.svc, hosted=True), JobQueue(storage, lambda p: fake.invoke(Payload=json.dumps(p).encode())), hosted=True)
        web.set_router(router, fake)

        self.site = self.root / "site"
        build_site(self.site)
        (self.site / "config.json").write_text(json.dumps({"hosted": True, "region": REGION, "userPoolId": self.pool, "clientId": self.client,
                                                           "cognitoEndpoint": ep + "/"}))
        self.csp = hosted_csp(REGION, f"127.0.0.1:{self.moto_port}", extra_connect=[ep])         # production's policy plus the stand-in endpoints
        outer = self

        class Edge(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def _send(self, status, body: bytes, ctype, extra=None):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Security-Policy", outer.csp)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
                outer.log.append((self.command, self.path.split("?")[0], status))

            def do_GET(self):
                self._route()

            def do_POST(self):
                self._route()

            def do_PUT(self):
                self._route()

            def do_DELETE(self):
                self._route()

            def _route(self):
                path = self.path.split("?")[0]
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                if path == "/_test/reject-next":                                              # a test control, not part of the product
                    outer.reject_next = True
                    return self._send(200, b"{}", "application/json")
                if path.startswith("/api/"):
                    return self._api(path, raw)
                f = "index.html" if path == "/" else path.lstrip("/")
                p = (outer.site / f).resolve()
                if self.command == "GET" and p.is_file() and outer.site.resolve() in p.parents:
                    return self._send(200, p.read_bytes(), TYPES.get(p.suffix, "application/octet-stream"))
                return self._send(403, b'{"message":"Forbidden"}', "application/json")          # a private bucket answers 403 for what is not there

            def _api(self, path, raw):
                if self.command not in ("GET", "POST"):
                    return self._send(404, b'{"message":"Not Found"}', "application/json")
                claims = None
                if path != "/api/health":
                    claims = outer.authorize(self.headers.get("Authorization", ""))
                    if claims is None:
                        return self._send(401, b'{"message":"Unauthorized"}', "application/json")
                event = {"rawPath": path, "rawQueryString": "", "headers": {k.lower(): v for k, v in self.headers.items()},
                         "requestContext": {"http": {"method": self.command}}, "body": raw.decode("utf-8") if raw else None, "isBase64Encoded": False}
                if claims is not None:
                    event["requestContext"]["authorizer"] = {"jwt": {"claims": claims}}
                r = web.handler(event)
                self._send(r["statusCode"], r["body"].encode("utf-8"), r["headers"].get("Content-Type", "application/json"),
                           {k: v for k, v in r["headers"].items() if k.lower() not in ("content-type",)})

        self.edge = ThreadingHTTPServer(("127.0.0.1", 0), Edge)
        self.edge.daemon_threads = True
        threading.Thread(target=self.edge.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.edge.server_address[1]}/"
        return self

    def stop(self) -> None:
        web.set_router(None, None)
        self.edge.shutdown()
        self.edge.server_close()
        self.moto.stop()

    # ---- the API Gateway authorizer stand-in -----------------------------------------------------------------------------------------
    def authorize(self, header: str) -> dict | None:
        if self.reject_next:
            self.reject_next = False
            return None
        if not header.startswith("Bearer "):
            return None
        try:
            part = header[7:].split(".")[1]
            c = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        except (ValueError, IndexError):
            return None
        if c.get("exp", 0) < time.time() or c.get("aud") != self.client or c.get("token_use") != "id":
            return None
        groups = c.get("cognito:groups", [])
        return {**{k: v for k, v in c.items() if k != "cognito:groups"}, "cognito:groups": "[" + " ".join(groups) + "]"}      # API Gateway's rendering
