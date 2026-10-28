# Plugging in Ingram's real material

Everything below changes configuration, not code.

## The question set
`python3 -m mra import-questions <questionnaire.xlsx> --version <label>` rebuilds `config/question_bank.json` from the 'Questions' sheet
(Section, Category, #, Question, Response Type, Rating 1 to 5). Answers in the file are ignored. If the headers differ, edit
`config/input_mapping.json` -> `questionnaire.columns`. The six scored sections, the other sections, the optional modules and their gate
questions are listed under `template_import`. The AWS question number is the stable id (`Q23`).
After an import run any questionnaire: the reader refuses a question number that is not in the bank and warns when wording differs.

## Workstreams and activities
`config/workstreams.json` maps every scored question to one readiness activity, groups activities into workstreams and gives each a phase
(Pre-Mobilize or Mobilize). It is inferred from one engagement. Replace it with the AWS tool's metadata when available; `load_config` refuses a scored question
that is not mapped.

## Scoring and colours
`config/rubric.json`: scale, rounding, colour thresholds (`red_below` is null until the level is known), the radar guide ring (80),
`action_below` (activities under it get an action), and the deck's list sizes and key-observation pages.

## Actions
`config/action_library.json`: the AWS standard actions and which activities trigger them. Activities below `action_below` with no library entry
get the next rating level's wording.

## The filled-in questionnaire
Column names and the Yes/No and text conventions are in `input_mapping.json` -> `questionnaire`. A rating must be a whole number from 1 to 5
(anything else is an error with its row); blank is allowed and shows as Incomplete. The canonical JSON shape (`ratings`, `answers`, `evidence`,
`meta`) is accepted too (`mra/loader.py: build_response_set`).

## Interview notes
A folder of `.txt` or `.md` files, a table, or a JSON list (`--evidence`). A first line `applies_to: Q26, BUS-04, BC-1` ties a note to questions,
categories or activities. PDF, Word and transcript readers are not built.

## The report templates
The deck uses the built-in AWS format unless you supply one (`--pptx-template`, or Settings in the page); see `docs/TEMPLATES.md`. `--docx-template` is optional.
Shapes are found by slide title, chart series name, activity name and question number. After changing templates run `python3 -m mra check-template <deck>` and
`tests/test_outputs.py` / `tests/test_deck_template.py` with `MRA_TEMPLATE_DECK=<deck>`.
After filling, the deck is scanned for the template's previous customer; leftovers are listed in the open-items file.

## Prompts
`config/prompts.json`. They change `config_hash`; the explanation cache is keyed by the prompt hash.
