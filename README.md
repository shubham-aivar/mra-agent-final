# A04 MRA Agent (Project Elevate), v0.8.0

Takes a filled-in **AWS Migration Readiness Assessment questionnaire** (plus interview notes), scores it, drafts the
observations and actions, runs the review and release gate, and writes the three AWS-format outputs: the **Questions /
Actions / Scores workbook**, the **Word readout** and the **PowerPoint readout deck**.

**Code does every calculation; a model only describes the facts it is given and may *propose* (a rating for a blank question,
evidence links, follow-up questions, planning context). A person accepts every proposal and releases the report.**
Scoring reproduces the AWS tool exactly (checked against a real engagement: every Scores-sheet value and every deck chart value).

**Status: question bank, scoring rules and workstream mapping are inferred from one real engagement; the red threshold,
the action library and the Strength/Gaps rule are proposals. Nothing has run on real Bedrock tool use or in real PowerPoint.**

## Try it (laptop, no AWS)
    python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
    python3 scripts/make_sample_questionnaire.py     # a synthetic filled-in questionnaire (one rating blank on purpose)
    python3 scripts/demo.py --step                   # narrated tour; add --pptx-template <AWS deck.pptx> to fill the deck too

## The review page (a browser UI on your laptop)
    python3 -m mra ui          # then upload the questionnaire and notes, review, and download or preview the Excel, Word and PowerPoint files
See `docs/UI.md`. The deck comes in the built-in AWS format; upload your own AWS-format deck to get the outputs in that format (`docs/TEMPLATES.md`).

## Hosted on AWS (CloudFront), for a shared demo
    scripts/deploy.sh --profile <name> --region us-east-1     # then scripts/add_user.py and scripts/smoke_test.py
See `docs/DEPLOY.md`.

## Step by step
    python3 -m mra run my_questionnaire.xlsx --set-id acme --customer "Acme" --evidence notes/ \
        --facilitator "Ingram Micro" --workshop-date 2026-10-23 --readout-date 2026-10-30
    python3 -m mra status acme                       # what blocks release (a blank rating shows as Incomplete, never 0)
    python3 -m mra adjust acme --question Q42 --score 1 --reason "No cloud team (answer to Q22)"
    python3 -m mra agent acme --task rating          # also: evidence, investigate, context (use --llm bedrock for a real model)
    python3 -m mra proposals acme ; python3 -m mra decide-proposals acme --accept P0001
    python3 -m mra decide acme --activity OM-1 --decision approve --owner "Name (role)" --due 2026-12-15
    python3 -m mra resolve acme --item no_evidence:Q41 --resolution accepted_risk --note "seen in the workshop"
    python3 -m mra confirm acme ; python3 -m mra finalise acme
    python3 -m mra export acme --xlsx --docx --pptx --out out/      # the deck uses the built-in AWS format; add --pptx-template <your deck.pptx> for yours

`export --pptx` fills Ingram's MAP deliverable template (the MRA slides; the sections that need other inputs stay blank; see `docs/TEMPLATES.md`) and also writes `*_open_items.md`: what a person must still do (cost slides, delivery-model variant, AMS fit score, next-step owners, logo).

## How scoring works (as in the AWS tool)
One agreed rating per question (1 to 5). A category, perspective, readiness activity, workstream or optional module scores the **plain mean of
its questions' ratings** (a perspective is the mean of its questions, not of its categories). Display: one decimal, rounded half up.
Radar: mean to three decimals, times 20. Green from 4.0; red is not configured yet (the level was never seen); amber otherwise.
A group with a blank rating is **Incomplete** (the AWS tool prints 0): no score, no colour, release blocked until it is rated.

## Where things are
    mra/inputs/      questionnaire reader, evidence reader, template importer (config/input_mapping.json holds column names)
    mra/scoring.py, focus.py, traceability.py, pipeline.py   calculations and the deck's rule-based sentences (no model, no storage)
    mra/explain/     prompts, checker, retries, cache, fallback
    mra/agent/       tools, workspace (read-only plus proposals), loop with limits, four tasks
    mra/review/      audit log and the release gate
    mra/outputs/     Excel, Word, PowerPoint, open-items and planning-note writers
    mra/service.py   every operation; the command line and the hosted API both call it
    hosted/ infra/   Lambda API handler and AWS CDK stack (never deployed)
    config/          question bank, workstreams, rubric, action library, input mapping, prompts
See `docs/DEPLOY.md`, `docs/UI.md`, `docs/TEMPLATES.md`, `docs/DESIGN.md`, `docs/INTEGRATION.md`, `docs/AGENT.md`, `docs/AWS_TEST.md`, `docs/DEPLOY.md`, `CHANGELOG.md`.
