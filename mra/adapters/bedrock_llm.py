"""Amazon Bedrock adapter (Converse API). Hosted mode and local runs with real model text use this.

boto3 is imported only when this adapter is created, so local runs need no AWS packages.
NOT yet verified against live Bedrock in this codebase: test it with `scripts/run_cli.py ... --llm bedrock`.
"""
from __future__ import annotations

from ..ports import ChatReply, LlmReply


class BedrockLlm:
    name = "bedrock"

    def __init__(self, model_id: str, region: str, client=None, timeout_s: int = 120):
        self.model_id = model_id
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client("bedrock-runtime", region_name=region,
                                  config=Config(retries={"max_attempts": 3, "mode": "standard"},
                                                read_timeout=timeout_s, connect_timeout=10))
        self.client = client

    def complete(self, system: str, user: str, max_tokens: int) -> LlmReply:
        r = self.client.converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
        )
        blocks = r["output"]["message"]["content"]
        text = "".join(b.get("text", "") for b in blocks)
        u = r.get("usage", {})
        return LlmReply(text, r.get("stopReason", "end_turn"), u.get("inputTokens", 0), u.get("outputTokens", 0),
                        self.model_id)

    def chat(self, system: str, messages: list, tools: list, max_tokens: int) -> ChatReply:
        """A tool-using turn through the Converse API. Not yet verified against live Bedrock."""
        kw = {"modelId": self.model_id, "system": [{"text": system}], "messages": messages,
              "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0}}
        if tools:
            kw["toolConfig"] = {"tools": [{"toolSpec": {"name": t["name"], "description": t["description"],
                                                         "inputSchema": {"json": t["schema"]}}} for t in tools]}
        r = self.client.converse(**kw)
        u = r.get("usage", {})
        return ChatReply(r["output"]["message"]["content"], r.get("stopReason", "end_turn"),
                         u.get("inputTokens", 0), u.get("outputTokens", 0))
