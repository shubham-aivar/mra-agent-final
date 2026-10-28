"""The two seams between the agent core and the outside world.

Local mode uses adapters/local_storage.py and adapters/llm_stub.py.
Hosted mode uses adapters/aws_storage.py (S3) and adapters/bedrock_llm.py behind the same interfaces,
so the core code does not change between local and hosted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def put_new(self, key: str, data: bytes) -> bool:
        """Create only if the key does not exist. Returns False if it already did."""
        ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def list(self, prefix: str) -> list[str]: ...
    def delete_prefix(self, prefix: str) -> int: ...


@dataclass(frozen=True)
class LlmReply:
    text: str
    stop_reason: str = "end_turn"      # "max_tokens" means the text was cut off
    input_tokens: int = 0
    output_tokens: int = 0
    model_id: str = ""


@dataclass(frozen=True)
class ChatReply:
    """One model turn in a tool-using conversation. 'content' holds blocks in Amazon Bedrock Converse format:
    {"text": ...} or {"toolUse": {"toolUseId", "name", "input"}}."""
    content: list
    stop_reason: str = "end_turn"      # "tool_use" means the model wants tools run; "max_tokens" means cut off
    input_tokens: int = 0
    output_tokens: int = 0


class Llm(Protocol):
    name: str          # "stub" or "bedrock"; used to label text provenance
    model_id: str

    def complete(self, system: str, user: str, max_tokens: int) -> LlmReply: ...

    def chat(self, system: str, messages: list, tools: list, max_tokens: int) -> ChatReply:
        """tools: [{"name", "description", "schema"}]. Used by the agent loop."""
        ...
