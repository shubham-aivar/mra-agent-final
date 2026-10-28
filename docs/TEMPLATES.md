# Deck templates

The PowerPoint output is filled into a deck template. Two are built in; you can add your own.

## 1. Ingram MAP deliverable (the default)
Ingram Micro's own template (`MAP_Access_Deliverable`), used exactly as supplied: the same 25 slides in the same order, the same layouts, colours, fonts and
pictures. **Nothing is added and nothing is removed**, and the build refuses to write a deck that would differ (slide count, order, layouts, shapes).

| Slides | What happens |
|---|---|
| 10 MRA Readiness - Recommendations | Chart of the average score per workstream; **Key Recommendations** = the AWS standard action behind each of the weakest activities (up to 5, weakest first; an action a reviewer rejected is not used) |
| 11 MRA Executive Summary | Strategy and Desired Outcomes (Q2) and MRA Objectives (Q1); 4 strengths and 4 gaps (the verified rule: Pre-Mobilize activities, weakest from the front, strongest from the back); perspectives radar |
| 12, 13 MRA Areas of Focus | The three rule-based sentences each, and the perspectives / workstreams radar charts |
| 14 MRA Readiness Scores | The eight workstream charts |
| 3 Executive Summary, 4 Assessment Scope, 7 MAP Journey (7R), 8 7R Analysis Details, 9 AWS Costs, 15 Stay On Premise Cost | **Left blank**: the slide, its labels and its tables stay; the `<>` fill-in markers, the sample customer's chart data and the red instruction text are removed. They need discovery, 7R or cost inputs, which this agent does not take yet. The open-items file lists each one and what it needs |
| 1, 2, 5, 6, 16 to 25 | Unchanged (title, agenda, 7R explainer, appendix, closing) |

The red "Example" markers over the template's sample data are removed. A chart label typed by hand in the template (a sample "3.8") is replaced by the real value; scores show one decimal.
A score that cannot be computed (a blank rating) leaves its bar and its radar point empty: never a 0.

**No bleeding, no overlapping.** Every text we write is fitted to its box: it keeps the template's font size when it fits, shrinks in half-point steps (floors: 11 pt answers, 12 pt sentences and recommendations, 10 pt lists) when
it does not, then flows long answers into one paragraph, and only then is cut at a word with an ellipsis. Boxes that reach into the layout's decorative stripe on the right are narrowed so text wraps before it.
Anything shortened is listed in the open-items file (the full text is in the Excel workbook). Fitting uses a deliberately wide character model, because the deck's font
(Poppins) may not be installed where it is opened.

## 2. AWS MRA readout format
The AWS deck (heatmap, observation tables, question-and-answer appendix). Choose it per export in the page, or pass it with `--pptx-template`
(`mra/outputs/templates/mra_readout_default.pptx`).

## Your own deck
Upload it in the page (Settings, *Add a deck*) or use `--pptx-template "my deck.pptx"`. A deck recognised as the Ingram format (its section titles) is filled the Ingram way,
anything else the AWS way. `python3 -m mra check-template "my deck.pptx"` (and the page) list what can be filled compared with the built-in deck.
A `.potx` PowerPoint template is accepted as it comes. `make-template` (AWS format) turns a filled deck into a clean template with a leak check.

## Adding an input later (7R, cost, discovery)
Each blank section is one slide found by its title (`BLANK` and `TITLES` in `mra/outputs/ingram_deck.py`). To fill one, add a method to `Filler` that writes that slide and remove its key from `BLANK`.

## Checking it
`tests/render_check.py` renders the deck (LibreOffice and pdftotext) and fails on text overlapping text or running into the right-hand stripe, counting only problems the template does not already have.

## Provenance
The audit log records the template's name and SHA-256 for each deck export. Fit measurements per text box are in the model (`deck_fit`) and covered by tests.

## Limits
- Only the MRA sections are filled; the Ingram deck's other data sections stay blank until their inputs exist.
- Fit is estimated, not measured by PowerPoint: open the deck once in PowerPoint to confirm on your machine.
- The Ingram template belongs to Ingram Micro; the AWS template content is AWS's. Keep this codebase within the engagement.
