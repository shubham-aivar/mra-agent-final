# Design record: A04 MRA Agent (v0.5)

## What the SOW says
A04 produces a scored MRA (capability scores, gaps, risks, recommended actions), an evidence traceability matrix (every score traceable),
a list of items needing stakeholder validation and a draft report. The Technical Consultant validates scores before release.
The SOW does **not** say how scores are calculated or that code must calculate them. Computing them in code is our design choice.

## What the real engagement showed (reference engagement questionnaire, Word report, deck, Scores sheet)
- One agreed answer per question, not one per person: a 1 to 5 rating, a Yes/No, or text. Each rating has five level descriptions;
  the chosen one is copied into the 'Rating Guide' column.
- 162 questions; 62 are scored ratings (Business 13, Platform 6, People 6, Governance 11, Operations 11, Security 15) in 35 categories
  (22 have one question). Scope and activity questions feed the deck's text slides. Nine optional modules are switched on by the scope answers.
- The reports regroup the same 62 ratings into 8 workstreams and 34 readiness activities (a second hierarchy).
- The AWS outputs have empty Observations and Actions: drafting them is the agent's job.
- There is no overall score in any AWS output.

## Decisions
| Decision | Why | Changes when |
|---|---|---|
| Scoring is code: the plain mean per group, half-up display rounding, radar = 3-decimal mean x 20 | Reproduces every Scores-sheet and deck-chart value of the real engagement (tested). A model doing this arithmetic would slip on the rounding traps. | Ingram supplies a different technique |
| A perspective is the mean of its questions, not of its categories | Matches the AWS values (the other way gives 3.1 instead of 3.0 for Business) | Never |
| **A blank rating makes its groups Incomplete** (AWS shows 0) | 0 is not on the 1 to 5 scale and hides good answers (People showed 0 although five of six were answered). Blocks confirm and release. | Ingram wants the AWS behaviour |
| The AWS 0 is kept as `aws_equivalent` | Back-testing against the AWS tool | Never |
| Colours: green from 4.0; red unset; amber otherwise | Seen: 2.0 to 3.3 amber, 4.0 green. The red level was never seen. | The red level is known (config/rubric.json) |
| A model may propose a rating for a blank question (quote verified in code); only a person's acceptance sets it | Judging which of five descriptions fits the evidence is the real consultant work; the roll-up stays code | Ingram disagrees |
| Deck sentences, strengths, gaps and key observations are rules (focus.py) | Reproduced exactly from the real deck; same wording every run | The AWS tool's rule turns out different |
| Tie-break: strongest = last in questionnaire order, weakest = first | Verified on three lists in the deck | Same |
| Strengths and gaps are chosen among Pre-Mobilize activities | The deck's executive-summary lists match only with that filter | Same |
| Phases come from the deck heatmap's diamond marker (14 Pre-Mobilize activities) | The only source seen | The AWS tool's metadata arrives |
| Action library: the AWS standard 10-row list; which activity triggers which row is Aivar's proposal; otherwise the next rating level's wording | The AWS Actions sheet was empty | Ingram's library arrives |
| Evidence never changes a rating; a note tagged to a question, category or activity counts as evidence | Traceability is its own measure | Ingram defines evidence differently |
| A confirmation is tied to a fingerprint of the scores; a later change makes it stale | F61 | Never |
| Review records that no longer fit new data are dropped and logged | A decision for a changed score must not carry over | Never |
| Report details (facilitator, workshop and readout dates) are required to finalise; unknown details print as TBD | The real deck had a customer name where the facilitator belongs and conflicting dates | Never |

## Not specified by the SOW for A04 (so chosen here)
Hosting (Lambda behind an API Gateway HTTP API, S3, Cognito), the model (Claude Opus 4.6 through a Bedrock inference profile),
and the review interface (command line and API; no web page).

## Known limits
- Question bank, workstream map, rubric, action library and prompts are inferred or proposed. Reports say so.
- The checker catches wrong numbers, colours, IDs and "already done" claims. It cannot tell whether a sentence is wise.
- Names in free text are not removed before a model sees them (only e-mail addresses are masked).
- The deck is filled from the built-in default (cleaned from the reference deck) or a deck you supply; both are verified by tests. Another layout is only filled where it matches, and the page says what it cannot fill.
- Things the questionnaire cannot supply stay visible in the open-items list: Mobilize cost slides, which delivery-model variant to keep,
  the AMS fit score (the AWS tool computes it), next-step owners and dates, the logo.
- Review state is last-write-wins: two reviewers acting at once can lose a decision. (The local page serialises its own actions with one lock.)
- Two ways to run the page: locally (one person, `python -m mra ui`) or hosted on CloudFront (several people; `docs/DEPLOY.md`). The hosted version has no MFA by request, no WAF and no custom domain.

## Verified versus not
Verified by automated tests: scoring against the AWS outputs, readers, importer, validation, review gate, agent guardrails with scripted
models, checker and retries, outputs (Excel layout, Word sections, deck fill on a synthetic template and on the real deck when
`MRA_TEMPLATE_DECK` is set), CLI, hosted handler with simulated events, S3 storage against a simulated S3, the CDK template.
Rendered and looked at: the filled deck, the Word report. Real AWS: text calls to Bedrock and S3 worked in v0.4.3.
Never run: Bedrock tool use (the agent tasks) and image input on the real model; real PowerPoint and Word; any deployment.
