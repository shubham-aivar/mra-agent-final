# The agent part of the MRA Agent

## What is an agent here, and what is not
Scoring, evidence status, validation lists, review and release are a fixed workflow: code, same steps every time.
Three tasks are different: the **model chooses what to look at next**, using tools, until it is done or hits a limit.

| Task | Goal | SOW anchor (A04) |
|---|---|---|
| `evidence` | Find interview-note passages that support scored questions with no evidence | "every score traces to evidence", evidence traceability KPI |
| `investigate` | Draft neutral follow-up questions for rating/guide disagreements, unverified high ratings and unevidenced ratings | "validation items are listed", follow-up rounds KPI |
| `rating` | Propose a 1 to 5 rating for a **blank** question, only when the notes or answers clearly match one level description | "every score traces to evidence"; a blank rating blocks release |
| `context` | Pull business priorities and constraints out of the notes | "MRA findings (readiness gaps, business priorities, constraints) are supplied to AWS Transform ... as business context" |

## The tools (the model can call only these)
Read: `list_unevidenced_questions`, `list_blank_ratings`, `get_question`, `list_validation_items`, `list_text_answers`, `list_notes`, `search_notes`, `read_note`, `list_activities`.
Propose: `propose_evidence_link`, `propose_followup`, `propose_context_item`, `propose_rating`.
No tool sets a score, changes an answer, makes a review decision, or writes a report. A test fails if one is added.

## Guardrails (all enforced in code, not by asking the model nicely)
- **Quotes are verified.** A proposed quote must appear word for word in the note (case and spacing ignored). Otherwise the tool
  returns an error and nothing is stored. A planted instruction in a note cannot make a model cite a passage that is not there.
- **Notes are untrusted.** The tools label note text as data, and the system prompt says so. Even a model that obeyed a planted
  instruction could only call the tools above, which cannot change anything that matters.
- **Arguments are checked** against a schema; unknown tools and malformed calls come back as errors.
- **Limits** (config/settings.json, `agent`): 15 model turns, 40 tool calls, 60,000 tokens per task. When one is hit the loop
  stops and keeps what exists. A model failure ends the run cleanly.
- **A person accepts every proposal.** Pending proposals change nothing. Accepted evidence links only change whether a question is
  traceable, never its score. An accepted rating proposal is recorded as a reviewer adjustment (who accepted, and which proposal). Accepting one resets the "scores confirmed" flag so the reviewer looks again.
- **Everything is audited:** the task start, every tool call, the stop reason, and the full conversation (kept as a hashed file).
- Emails in notes and answers are masked before the model sees them. Names in free text are not removed.

## Using it (laptop, stub model: no AWS)
    python -m mra agent <run> --task evidence          # the model looks, then proposes
    python -m mra proposals <run> --status proposed    # read each one, with its exact quote
    python -m mra decide-proposals <run> --accept P0001 P0002 --reject P0003 --comment "unrelated"
    python -m mra agent <run> --task investigate
    python -m mra agent <run> --task context
    python -m mra export <run> --context               # the plain-language note for AWS Transform planning
    python -m mra kpis <run>                           # turnaround, traceability, acceptance rate (SOW KPIs)
    python -m mra log-hours <run> --hours 3.5          # effort the agent cannot measure itself
Add `--llm bedrock` to any `agent` command to use the real model. **The stub is a fixed script, not a model**: it always
searches by category name and quotes the best sentence, and it never proposes a rating. It exists so the loop, tools and review flow can be run and tested offline.

## What is not verified
- The Bedrock tool-use calls (`BedrockLlm.chat`, Converse `toolConfig`) have never run against live Bedrock.
- How well a real model chooses what to read, and how often the quote check rejects it, is unknown until it runs on real notes.
- Real interview notes are not available; everything was tested on synthetic notes.
- The context note's priorities and constraints are only as good as the notes. Nothing is sent to AWS Transform automatically.
- Follow-up drafts are never sent; a person sends them (Forms, e-mail, a call).
- KPI turnaround is measured from the first run, not from when responses arrived. Analyst hours must be logged by a person.
