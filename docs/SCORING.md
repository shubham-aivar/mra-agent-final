# MRA Agent: scoring method


## 1. What this document covers

How the A04 MRA Agent turns ratings into scores, colours and review items, and where each rule comes from. Everything below is taken from the code (mra/scoring.py, mra/traceability.py, mra/focus.py, config/rubric.json). The worked examples were produced by calling the real functions.

Principle: code does all scoring. A model never produces or changes a score. It can only propose (a rating for a blank question, an evidence passage, a follow-up), every proposal is quote-checked, and a person accepts or rejects it.


## 2. Where the questionnaire comes from, and what AWS does and does not publish

Published by AWS (checked on the pages below):

- The Migration Readiness Assessment (MRA) is the prescriptive model for the assessment phase of the AWS migration strategy. Its purpose is to show how far along an organization is, its readiness strengths and weaknesses, and to build an action plan to close the gaps.
- The AWS Cloud Adoption Framework (CAF) has six perspectives: business, people, governance, platform, security and operations. Our six perspectives follow these.
- The assessment is run as a facilitated meeting using a CAF-aligned set of discussion starters (the AWS Assessment Tool is named as an example). The output is an out-brief deck with observations and next steps.
- Results are shown as a heat map of strengths and weaknesses. Areas in yellow or red need action plans.

Not published by AWS (in the pages we could check): any scoring formula, any numeric threshold for yellow or red, and a question count.

So the scoring rules in section 3 are INFERRED. We took the question bank and the scoring rules from a reference MRA export (an AWS-format Excel file with its Word report and deck) and confirmed that the rules reproduce every score, every radar value and every chart value in it exactly. The question bank (config/question_bank.json) is imported from that export: 162 question rows, of which 62 are scored 1 to 5 ratings, in 6 perspectives and 35 categories, plus 9 optional modules. A second hierarchy groups the same questions into 8 workstreams and 34 readiness activities. Answers in the export were ignored; only template content was imported.

> Open points to confirm with the customer or AWS: (a) the red threshold, unknown, so no group is ever coloured red; (b) whether the AWS Assessment Tool uses exactly these rules; (c) the AWS guide pages we checked state no question count, so the 162 vs 62 split rests on the export, not on AWS text.

References (AWS documentation):

- Evaluating migration readiness (what MRA is, the six CAF perspectives): https://docs.aws.amazon.com/prescriptive-guidance/latest/migration-readiness/welcome.html
- Readiness assessment process (facilitated meeting, discussion starters, out-brief deck): https://docs.aws.amazon.com/prescriptive-guidance/latest/migration-readiness/process.html
- Targeted business outcomes (heat map, yellow and red areas need action plans): https://docs.aws.amazon.com/prescriptive-guidance/latest/migration-readiness/targeted-business-outcomes.html
- Migration readiness, full guide (PDF): https://docs.aws.amazon.com/pdfs/prescriptive-guidance/latest/migration-readiness/migration-readiness.pdf
- Assess phase, AWS migration strategy: https://docs.aws.amazon.com/prescriptive-guidance/latest/strategy-migration/assessment-phase.html
- AWS Cloud Adoption Framework overview (perspectives): https://docs.aws.amazon.com/whitepapers/latest/overview-aws-cloud-adoption-framework/foundational-capabilities.html


## 3. The scoring rules


### 3.1 Rating

Every scored question has one agreed rating, a whole number from 1 to 5. Each rating has a level text in the questionnaire (what that level means). A reviewer adjustment replaces the rating; the original is kept and shown.


### 3.2 Group score

A group is a category, perspective, activity, workstream or optional module. Its score is the plain mean of the ratings of the questions in it.

- mean = sum of ratings / number of questions in the group
- Every question weighs the same. A perspective is the mean of ALL its questions, not the mean of its category scores.
- Displayed score = mean rounded half up to 1 decimal (3.25 shows 3.3, never 3.2).
- Internal mean = mean rounded half up to 3 decimals.
- Radar value = internal mean x 20, rounded half up to 2 decimals (so a mean of 3.308 plots as 66.16). The radar guide line is at 80, which is a mean of 4.0.
- Rounding is done on the decimal text of the number, so binary-float effects cannot change a result.


### 3.3 Incomplete groups

If any question in a group has no rating, the group is Incomplete: no score, no colour, no radar point. Release is blocked until it is rated. AWS's tool shows 0 for such a group, which would read as 'not ready'. We keep that number as aws_equivalent for back-testing only and never show it as a score.


### 3.4 Colour

| Condition | Colour |
|---|---|
| Score at or above 4.0 (green_from) | Green |
| Score below the red level (red_below) | Red. Not used yet: the red level is unknown (null) |
| Any other score | Amber |
| Group is Incomplete | No colour |

Colour is decided on the displayed (1 decimal) score. A mean of 3.96 displays 4.0 and is therefore green.


### 3.5 Worked examples (computed by the code)

| Ratings in the group | Mean (3 dp) | Score shown | Radar | Colour | AWS tool would show |
|---|---|---|---|---|---|
| 4, 3, 3, 3 | 3.25 | 3.3 | 65.0 | amber | 3.3 |
| 5, 4, 4, 4, 4 | 4.2 | 4.2 | 84.0 | green | 4.2 |
| 3, 3, 4, 4, 4, 3, 3, 3, 3, 4, 3, 3, 3 | 3.308 | 3.3 | 66.16 | amber | 3.3 |
| 3, 4, 4, 4 | 3.75 | 3.8 | 75.0 | amber | 3.8 |
| 4, 4, blank | none | none | none | incomplete | 0.0 |

> Row 1: 13 / 4 = 3.25, which shows 3.3 (half up). Row 3: 43 / 13 = 3.3077, internal mean 3.308, radar 3.308 x 20 = 66.16. Row 5: one blank rating, so the group is Incomplete.


## 4. Hierarchy: how questions roll up

| Level | What it is | Rule |
|---|---|---|
| Question | 62 scored ratings (plus 100 non-rating rows) | Rating 1 to 5 |
| Category | 35, each inside one perspective | Mean of its questions |
| Perspective | 6 (CAF): business, people, governance, platform, security, operations | Mean of all its questions |
| Activity | 34 readiness activities, each listing its questions | Mean of its questions |
| Workstream | 8, each listing its activities | Mean of all questions in its activities |
| Optional module | 9, never block a report | Mean of its questions |


## 5. Actions and what counts as a gap

- An activity needs action when it is complete and its score is below 4.0 (action_below).
- Its actions come from the action library (config) for that activity. If the library has none, the fallback text is used with the next level text of the lowest-rated question in the activity (ties go to the lower question number).
- Each action starts unassigned. A reviewer approves, edits or rejects it and an approved action needs an owner and a due date before release.
- The observation text for an activity is the questionnaire's own level text for its ratings. A model may rewrite it, but the rewrite is labelled and checked against the facts.


## 6. Validation items: what the reviewer is asked to check

Each item names a question (for example Q24). The dashboard, Excel, Word, CSV and open-items file show the question text, its category and perspective, the rating and what that level says, and what to do about it. Optional modules never create items.

| Kind | Raised when | How the analyst reviews it |
|---|---|---|
| blank_rating | A question has no rating | Give it a rating on the Ratings tab, or accept an agent rating proposal. It cannot be resolved with a note. |
| guide_mismatch | The rating differs from the level the Rating Guide text implies and has not been adjusted | Read both levels, adjust the rating with a reason, or resolve with a note. |
| unverified_high_claim | Rated 4 or 5 (high_claim_at_or_above 4.0) with no supporting evidence | Find the passage in the notes (Evidence task on the Proposals tab can propose one). If it cannot be verified, lower the rating or accept the risk with a note. |
| no_evidence | Rated below 4 with no supporting evidence | Add evidence, or accept the risk with a note. |

Order: by kind in the order above, then by question number. Items are numbered V001, V002 and so on in that order. Example: V002 unverified_high_claim: Q24 is rated 4 with no supporting evidence.


### Evidence rule

A rating is traceable only if at least one evidence passage is linked to it. A passage links to a question directly, through its category, or through its activity, or when an accepted agent proposal links it. Every proposed passage must be found word for word in the stored note, otherwise it is shown as not located. Evidence can support a rating; it never changes one.


## 7. Release gate

A report cannot be finalised while any of these stand: a blank rating; an activity that needs action with no decision, owner or due date; a validation item (other than blank ratings) with no resolution and note; scores not confirmed by a reviewer; report details missing. Each blocker names the question and its category.


## 8. Summary lines in the deck and report

All lists use complete groups only (Incomplete groups are listed separately). They are sorted by score ascending, with equal scores kept in questionnaire order. The weakest N are taken from the front of that list, the strongest N from the back, so among equal scores the later one in questionnaire order counts as strongest.

- Strengths and gaps use the Pre-Mobilize activities: gaps = the 4 lowest, strengths = the 2 highest (strengths_n, gaps_n).
- Key observations are given per page of workstreams (key_observation_pages): within the page's activities, the 3 lowest and the 2 highest (key_weak_n, key_strong_n).
- Perspective sentence: the strongest perspective and its strongest category, the weakest perspective and its weakest category, and the second weakest perspective with its weakest category. The workstream sentences follow the same pattern with workstreams.
- The sentences are fixed templates filled with these names, so the same ratings always give the same text. The counts are set in config/rubric.json (presentation).


## 9. Configuration and change control

All thresholds live in config/rubric.json (version inferred-0.1, status INFERRED). When the customer or AWS confirms the red level or any other rule, change it there; the config hash is recorded with every run, so a result can always be traced to the rules that produced it.
