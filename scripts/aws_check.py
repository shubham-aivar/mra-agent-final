"""Check each AWS link the agent uses, one at a time, with the smallest possible calls. Deploys nothing.

    python scripts/aws_check.py --bucket <your-bucket>          # identity, Bedrock text, Bedrock tool use, S3
    python scripts/aws_check.py --skip-s3                       # Bedrock only
    python scripts/aws_check.py --bucket <b> --skip-bedrock     # S3 only

It creates nothing except a few tiny probe objects (under a unique 'probe-xxxxxxxx/' prefix) in the bucket you name,
and deletes exactly those. Each Bedrock check is one or two short calls (a few hundred tokens).
Sign in first: aws sso login --profile <profile> and export AWS_PROFILE=<profile>.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mra.agent.schema import validate  # noqa: E402
from mra.explain.explainer import _strip_fences  # noqa: E402

Result = tuple[str, str, str]      # (check, PASS | FAIL | SKIP | INFO, detail)

HINTS = {
    "NoCredentialsError": "no credentials found: set AWS_PROFILE and run `aws sso login --profile <profile>`",
    "ExpiredToken": "your sign-in expired: run `aws sso login --profile <profile>`",
    "ExpiredTokenException": "your sign-in expired: run `aws sso login --profile <profile>`",
    "TokenRetrievalError": "your sign-in expired: run `aws sso login --profile <profile>`",
    "UnrecognizedClientException": "AWS did not recognise these credentials: check AWS_PROFILE",
    "AccessDeniedException": "this role may not call Bedrock, or model access is not enabled for this model in this Region "
                             "(Bedrock console > Model access)",
    "ValidationException": "Bedrock rejected the request: check the model id (MRA_MODEL_ID) and the Region",
    "ResourceNotFoundException": "model or inference profile not found in this Region: check MRA_MODEL_ID and --region",
    "ThrottlingException": "throttled by Bedrock: wait a minute and run again",
    "NoSuchBucket": "the bucket does not exist, or is in a different Region",
    "AccessDenied": "this role has no permission on that bucket",
    "403": "this role has no permission on that bucket",
    "404": "the bucket does not exist, or is in a different Region",
}


def explain_error(e: Exception) -> str:
    code = getattr(e, "response", {}).get("Error", {}).get("Code") or type(e).__name__
    return f"{code}: {HINTS.get(code, 'unexpected error')} ({str(e)[:160]})"


def check_identity(sts) -> list[Result]:
    r = sts.get_caller_identity()
    kind = "an assumed role" if ":assumed-role/" in r["Arn"] else "an IAM user" if ":user/" in r["Arn"] else "another identity"
    return [("identity", "PASS", f"signed in to the account ending {r['Account'][-4:]} as {kind}")]


def check_bedrock_text(llm) -> list[Result]:
    t = time.time()
    r = llm.complete("Reply with JSON only.", 'Return exactly {"ok": true}', 60)
    ok = json.loads(_strip_fences(r.text)).get("ok") is True
    return [("bedrock text", "PASS" if ok else "FAIL",
             f"{llm.model_id} answered in {time.time() - t:.1f}s, {r.input_tokens + r.output_tokens} tokens"
             + ("" if ok else f"; unexpected reply: {r.text[:80]!r}"))]


ECHO = {"name": "echo", "description": "Echo back some text.",
        "schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}


def check_bedrock_tools(llm) -> list[Result]:
    """One full tool round trip: the model asks for a tool, we answer, the model finishes. This is what the agent loop does."""
    msgs = [{"role": "user", "content": [{"text": "Call the echo tool once with the text 'hello', then say what it returned."}]}]
    r1 = llm.chat("You must use the tool you are given.", msgs, [ECHO], 300)
    uses = [b["toolUse"] for b in r1.content if "toolUse" in b]
    if r1.stop_reason != "tool_use" or not uses:
        return [("bedrock tool use", "FAIL", f"the model did not ask for the tool (stop reason {r1.stop_reason!r})")]
    u = uses[0]
    problem = validate(u.get("input"), ECHO["schema"])
    if u["name"] != "echo" or problem:
        return [("bedrock tool use", "FAIL", f"the tool request was malformed: {u['name']} {problem}")]
    msgs += [{"role": "assistant", "content": r1.content},
             {"role": "user", "content": [{"toolResult": {"toolUseId": u["toolUseId"], "status": "success",
                                                          "content": [{"json": {"echo": u["input"]["text"]}}]}}]}]
    r2 = llm.chat("You must use the tool you are given.", msgs, [ECHO], 300)
    said = " ".join(b["text"] for b in r2.content if "text" in b).strip()
    if r2.stop_reason != "end_turn" or not said:
        return [("bedrock tool use", "FAIL", f"after the tool result the model did not finish (stop reason {r2.stop_reason!r})")]
    tokens = r1.input_tokens + r1.output_tokens + r2.input_tokens + r2.output_tokens
    return [("bedrock tool use", "PASS",
             f"request, result and answer all worked ({tokens} tokens); the model said: {said[:60]!r}")]


def check_s3(storage, s3, bucket: str) -> list[Result]:
    out: list[Result] = []
    s3.head_bucket(Bucket=bucket)
    out.append(("s3 bucket", "PASS", f"{bucket} exists and this role can reach it"))
    prefix = f"probe-{uuid.uuid4().hex[:8]}/"
    try:
        storage.put(prefix + "a", b"1")
        got = storage.get(prefix + "a")
        first, second = storage.put_new(prefix + "b", b"1"), storage.put_new(prefix + "b", b"2")
        kept = storage.get(prefix + "b")
        listed = storage.list(prefix)
        ok = got == b"1" and first is True and second is False and kept == b"1" and len(listed) == 2
        out.append(("s3 read/write", "PASS" if got == b"1" and len(listed) == 2 else "FAIL", "write, read and list worked"))
        out.append(("s3 no-overwrite write", "PASS" if first is True and second is False and kept == b"1" else "FAIL",
                    "a second write to the same key was refused, so the audit log cannot be overwritten" if ok else
                    f"first={first} second={second} kept={kept!r}: the audit log's protection would NOT work on this bucket"))
    finally:
        removed = storage.delete_prefix(prefix)
        out.append(("s3 cleanup", "PASS" if removed == 2 and storage.list(prefix) == [] else "FAIL",
                    f"removed {removed} probe objects"))
    try:
        enc = s3.get_bucket_encryption(Bucket=bucket)["ServerSideEncryptionConfiguration"]["Rules"][0]
        out.append(("s3 encryption", "INFO", f"default encryption: {enc['ApplyServerSideEncryptionByDefault']['SSEAlgorithm']}"))
    except Exception as e:  # informational only
        out.append(("s3 encryption", "INFO", f"could not read it ({type(e).__name__})"))
    try:
        pab = s3.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"]
        blocked = all(pab.values())
        out.append(("s3 public access", "PASS" if blocked else "FAIL",
                    "all four public-access blocks are on" if blocked else f"public access is not fully blocked: {pab}"))
    except Exception as e:
        out.append(("s3 public access", "FAIL", f"could not confirm public access is blocked ({type(e).__name__})"))
    return out


def run_checks(sts, llm, s3, storage, bucket: str | None, skip_bedrock: bool, skip_s3: bool) -> list[Result]:
    results: list[Result] = []
    steps = [("identity", lambda: check_identity(sts))]
    if not skip_bedrock:
        steps += [("bedrock text", lambda: check_bedrock_text(llm)), ("bedrock tool use", lambda: check_bedrock_tools(llm))]
    if not skip_s3:
        steps += [("s3", lambda: check_s3(storage, s3, bucket))] if bucket else []
    for name, fn in steps:
        try:
            results += fn()
        except Exception as e:
            results.append((name, "FAIL", explain_error(e)))
            if name == "identity":                      # nothing else can work without a sign-in
                break
    if not skip_s3 and not bucket:
        results.append(("s3", "SKIP", "no --bucket given"))
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--model-id", default="us.anthropic.claude-opus-4-6-v1")
    ap.add_argument("--bucket")
    ap.add_argument("--skip-bedrock", action="store_true")
    ap.add_argument("--skip-s3", action="store_true")
    a = ap.parse_args(argv)
    import boto3

    from mra.adapters.aws_storage import AwsStorage
    from mra.adapters.bedrock_llm import BedrockLlm
    s3 = boto3.client("s3", region_name=a.region)
    results = run_checks(boto3.client("sts", region_name=a.region), BedrockLlm(a.model_id, a.region), s3,
                         AwsStorage(a.bucket, a.region, client=s3) if a.bucket else None, a.bucket, a.skip_bedrock, a.skip_s3)
    print(f"Region {a.region}, model {a.model_id}\n")
    for name, status, detail in results:
        print(f"  {status:<5} {name:<22} {detail}")
    failed = [r for r in results if r[1] == "FAIL"]
    print("\n" + ("All checks passed." if not failed else f"{len(failed)} check(s) failed. Fix the first one and run again."))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
