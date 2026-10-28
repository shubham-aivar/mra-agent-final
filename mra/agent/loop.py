"""The agent loop: the model decides which tool to call next, code runs the tool and checks every argument.

Limits: a number of model turns, a number of tool calls, and a total token budget. When any limit is reached the
loop stops and keeps whatever proposals exist. A tool problem is returned to the model as an error result and never
crashes the run. Nothing the model says is trusted: unknown tools and badly shaped arguments are refused.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ..ports import Llm
from .schema import validate
from .tools import Tool
from .workspace import ToolError, Workspace

log = logging.getLogger(__name__)


@dataclass
class LoopResult:
    stop_reason: str = "done"      # done, budget_steps, budget_tool_calls, budget_tokens, truncated, error
    steps: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    final_text: str = ""
    messages: list = field(default_factory=list)


def _run_tool(tools: dict[str, Tool], ws: Workspace, use: dict) -> tuple[dict, dict]:
    """Run one tool request. Returns (toolResult block, event for the audit log)."""
    name, args, uid = use.get("name", ""), use.get("input", {}), use.get("toolUseId", "")
    tool = tools.get(name)
    if tool is None:
        problem = f"there is no tool called '{name}'"
    else:
        problem = validate(args, tool.schema)
    if problem is None:
        try:
            out = tool.fn(ws, args)
            return ({"toolResult": {"toolUseId": uid, "content": [{"json": out}], "status": "success"}},
                    {"tool": name, "input": args, "outcome": "ok"})
        except ToolError as e:
            problem = str(e)
        except Exception:  # a bug in a tool must not stop the run; details go to the log, not to the model
            log.exception("tool %s failed", name)
            problem = "the tool failed; try something else"
    return ({"toolResult": {"toolUseId": uid, "content": [{"text": problem}], "status": "error"}},
            {"tool": name, "input": args, "outcome": "error", "error": problem})


def run_loop(llm: Llm, system: str, user: str, tools: dict[str, Tool], ws: Workspace, *, max_steps: int,
             max_tool_calls: int, max_total_tokens: int, max_output_tokens: int,
             on_event: Callable[[dict], None]) -> LoopResult:
    out = LoopResult(messages=[{"role": "user", "content": [{"text": user}]}])
    specs = [{"name": t.name, "description": t.description, "schema": t.schema} for t in tools.values()]
    for step in range(max_steps):
        try:
            reply = llm.chat(system, out.messages, specs, max_output_tokens)
        except Exception as e:
            on_event({"tool": None, "outcome": "model_error", "error": f"{type(e).__name__}: {str(e)[:200]}"})
            out.stop_reason = "error"
            return out
        out.steps = step + 1
        out.input_tokens += reply.input_tokens
        out.output_tokens += reply.output_tokens
        out.messages.append({"role": "assistant", "content": reply.content})
        if reply.stop_reason == "max_tokens":
            out.stop_reason = "truncated"           # a cut-off turn may hold half a tool request: do not run it
            return out
        uses = [b["toolUse"] for b in reply.content if "toolUse" in b]
        if reply.stop_reason != "tool_use" or not uses:
            out.final_text = " ".join(b["text"] for b in reply.content if "text" in b).strip()
            out.stop_reason = "done"
            return out
        if out.tool_calls + len(uses) > max_tool_calls:
            out.stop_reason = "budget_tool_calls"
            return out
        results = []
        for use in uses:
            block, event = _run_tool(tools, ws, use)
            out.tool_calls += 1
            on_event(event)
            results.append(block)
        out.messages.append({"role": "user", "content": results})
        if out.input_tokens + out.output_tokens > max_total_tokens:
            out.stop_reason = "budget_tokens"
            return out
    out.stop_reason = "budget_steps"
    return out
