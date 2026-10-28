from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    llm_mode: str = "stub"
    model_id: str = "us.anthropic.claude-opus-4-6-v1"
    region: str = "us-east-1"
    max_tokens: int = 3000
    batch_size: int = 4
    max_retries: int = 2
    storage_mode: str = "local"
    local_root: str = ".local"
    bucket: str | None = None
    mask_emails_in_prompts: bool = True
    agent_max_steps: int = 15
    agent_max_tool_calls: int = 40
    agent_max_tokens: int = 60000


def load_settings(path: str | Path = "config/settings.json", env: dict | None = None) -> Settings:
    env = os.environ if env is None else env
    raw = json.loads(Path(path).read_text()) if Path(path).exists() else {}
    llm, st, ag = raw.get("llm", {}), raw.get("storage", {}), raw.get("agent", {})
    s = Settings(
        llm_mode=env.get("MRA_LLM_MODE", llm.get("mode", "stub")),
        model_id=env.get("MRA_MODEL_ID", llm.get("model_id", Settings.model_id)),
        region=env.get("MRA_REGION", llm.get("region", Settings.region)),
        max_tokens=int(llm.get("max_tokens", Settings.max_tokens)),
        batch_size=int(llm.get("batch_size", Settings.batch_size)),
        max_retries=int(llm.get("max_retries", Settings.max_retries)),
        storage_mode=env.get("MRA_STORAGE_MODE", st.get("mode", "local")),
        local_root=env.get("MRA_LOCAL_ROOT", st.get("local_root", ".local")),
        bucket=env.get("MRA_BUCKET", st.get("bucket")),
        mask_emails_in_prompts=bool(raw.get("mask_emails_in_prompts", True)),
        agent_max_steps=int(ag.get("max_steps", Settings.agent_max_steps)),
        agent_max_tool_calls=int(ag.get("max_tool_calls", Settings.agent_max_tool_calls)),
        agent_max_tokens=int(ag.get("max_total_tokens", Settings.agent_max_tokens)),
    )
    problems = []
    if s.llm_mode not in ("stub", "bedrock"):
        problems.append(f"llm mode must be 'stub' or 'bedrock', got '{s.llm_mode}'")
    if s.storage_mode not in ("local", "aws"):
        problems.append(f"storage mode must be 'local' or 'aws', got '{s.storage_mode}'")
    if s.storage_mode == "aws" and not s.bucket:
        problems.append("storage mode 'aws' needs a bucket (settings or MRA_BUCKET)")
    if not 1 <= s.batch_size <= 10:
        problems.append("batch_size must be 1 to 10")
    if not 0 <= s.max_retries <= 5:
        problems.append("max_retries must be 0 to 5")
    if not 1 <= s.agent_max_steps <= 40:
        problems.append("agent max_steps must be 1 to 40")
    if not 1 <= s.agent_max_tool_calls <= 200:
        problems.append("agent max_tool_calls must be 1 to 200")
    if not 1000 <= s.agent_max_tokens <= 500000:
        problems.append("agent max_total_tokens must be 1000 to 500000")
    if problems:
        raise ValueError("; ".join(problems))
    return s
