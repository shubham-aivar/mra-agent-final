import pytest

from mra.adapters.bedrock_llm import BedrockLlm
from mra.adapters.factory import build_llm, build_storage
from mra.adapters.llm_stub import StubLlm
from mra.adapters.local_storage import LocalStorage
from mra.settings import load_settings


def test_local_storage_basics(tmp_path):
    s = LocalStorage(tmp_path)
    s.put("a/b.txt", b"1")
    assert s.get("a/b.txt") == b"1" and s.exists("a/b.txt") and not s.exists("a/c.txt")
    assert s.put_new("a/c.txt", b"x") is True and s.put_new("a/c.txt", b"y") is False and s.get("a/c.txt") == b"x"
    assert s.list("a/") == ["a/b.txt", "a/c.txt"] and s.list("zzz") == []
    assert s.delete_prefix("a/") == 2 and s.list("") == [] and not (tmp_path / "a").exists()


@pytest.mark.parametrize("key", ["../evil", "a/../../evil", "/etc/passwd"])
def test_local_storage_blocks_escapes(tmp_path, key):
    with pytest.raises(ValueError):
        LocalStorage(tmp_path / "root").put(key, b"x")


def test_settings_defaults_overrides_and_validation(tmp_path):
    f = tmp_path / "s.json"
    f.write_text('{"llm": {"mode": "stub"}, "storage": {"local_root": "x"}}')
    s = load_settings(f, env={})
    assert (s.llm_mode, s.storage_mode, s.local_root, s.batch_size) == ("stub", "local", "x", 4)
    s = load_settings(f, env={"MRA_LLM_MODE": "bedrock", "MRA_STORAGE_MODE": "aws", "MRA_BUCKET": "b", "MRA_REGION": "eu-west-1"})
    assert (s.llm_mode, s.storage_mode, s.bucket, s.region) == ("bedrock", "aws", "b", "eu-west-1")
    for env, msg in [({"MRA_LLM_MODE": "gpt"}, "llm mode"), ({"MRA_STORAGE_MODE": "aws"}, "needs a bucket"),
                     ({"MRA_STORAGE_MODE": "cloud"}, "storage mode")]:
        with pytest.raises(ValueError, match=msg):
            load_settings(f, env=env)
    assert load_settings(tmp_path / "missing.json", env={}).llm_mode == "stub"


def test_factory_builds_local_and_stub(tmp_path):
    s = load_settings(tmp_path / "none.json", env={"MRA_LOCAL_ROOT": str(tmp_path)})
    assert isinstance(build_storage(s), LocalStorage) and isinstance(build_llm(s), StubLlm)


class FakeBedrockClient:
    def __init__(self, reply):
        self.reply, self.kwargs = reply, None

    def converse(self, **kw):
        self.kwargs = kw
        return self.reply


def test_bedrock_adapter_builds_the_converse_request_and_reads_the_reply():
    reply = {"output": {"message": {"content": [{"text": "hel"}, {"text": "lo"}]}}, "stopReason": "max_tokens",
             "usage": {"inputTokens": 12, "outputTokens": 3}}
    c = FakeBedrockClient(reply)
    r = BedrockLlm("us.anthropic.claude-opus-4-6-v1", "us-east-1", client=c).complete("SYS", "USER", 500)
    assert c.kwargs["modelId"] == "us.anthropic.claude-opus-4-6-v1" and c.kwargs["system"] == [{"text": "SYS"}]
    assert c.kwargs["messages"] == [{"role": "user", "content": [{"text": "USER"}]}]
    assert c.kwargs["inferenceConfig"] == {"maxTokens": 500, "temperature": 0}
    assert (r.text, r.stop_reason, r.input_tokens, r.output_tokens) == ("hello", "max_tokens", 12, 3)


def test_bedrock_errors_surface_so_the_explainer_can_retry():
    class Boom:
        def converse(self, **kw):
            raise RuntimeError("throttled")
    with pytest.raises(RuntimeError):
        BedrockLlm("m", "r", client=Boom()).complete("s", "u", 10)
