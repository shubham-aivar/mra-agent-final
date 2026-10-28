"""Build the storage and model adapters from settings. AWS packages are imported only when asked for."""
from __future__ import annotations

from ..ports import Llm, Storage
from ..settings import Settings


def build_storage(s: Settings) -> Storage:
    if s.storage_mode == "aws":
        from .aws_storage import AwsStorage
        return AwsStorage(s.bucket, s.region)
    from .local_storage import LocalStorage
    return LocalStorage(s.local_root)


def build_llm(s: Settings) -> Llm:
    if s.llm_mode == "bedrock":
        from .bedrock_llm import BedrockLlm
        return BedrockLlm(s.model_id, s.region)
    from .llm_stub import StubLlm
    return StubLlm()
