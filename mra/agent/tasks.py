"""The three agent tasks. Each gives the model a goal and a set of tools; the model chooses what to read and what to propose."""
from __future__ import annotations

from collections.abc import Callable

from ..ports import Llm
from .loop import run_loop
from .tools import TASK_TOOLS, tools_for
from .workspace import Workspace

TASKS = tuple(TASK_TOOLS)


def _user_prompt(task: str, ws: Workspace) -> str:
    notes = len(ws.notes)
    if task == "evidence":
        n = ws.unevidenced_questions()["count"]
        return (f"There are {n} rated questions with no supporting evidence and {notes} notes. Find evidence in the notes "
                "where it clearly exists, and propose links with exact quotes.")
    if task == "investigate":
        return (f"There are {len(ws.items)} items for a person to validate, and {notes} notes. Propose targeted follow-up "
                "questions for the ones where a question to a stakeholder would help.")
    if task == "rating":
        n = ws.blank_ratings()["count"]
        return (f"There are {n} blank ratings and {notes} notes. For each blank rating, propose a rating only if the notes or the "
                "questionnaire answers clearly match one of its five level descriptions.")
    return (f"There are {notes} notes and {sum(a['needs_action'] for a in ws.result['activities'])} activities that need an action. "
            "Extract the business priorities and constraints that migration planning should know about.")


def run_agent_task(task: str, ws: Workspace, llm: Llm, prompts: dict, *, max_steps: int, max_tool_calls: int,
                   max_total_tokens: int, max_output_tokens: int, on_event: Callable[[dict], None]) -> dict:
    if task not in TASKS:
        raise ValueError(f"unknown agent task '{task}' (available: {list(TASKS)})")
    system = prompts["agent"]["common"] + "\n\n" + prompts["agent"][task]
    r = run_loop(llm, system, _user_prompt(task, ws), tools_for(task), ws, max_steps=max_steps, max_tool_calls=max_tool_calls,
                 max_total_tokens=max_total_tokens, max_output_tokens=max_output_tokens, on_event=on_event)
    return {"task": task, "stop_reason": r.stop_reason, "steps": r.steps, "tool_calls": r.tool_calls,
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "summary": r.final_text,
            "proposals_created": len(ws.new), "messages": r.messages}
