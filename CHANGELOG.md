# Changelog

## 0.8.1 (fixes from real PowerPoint renders)
- Long answers (executive summary) no longer collapse to one word and "..." per line or overflow onto the radar: lines are kept whole, or flowed, at a readable size
  (11 pt minimum for answers, 12 pt for sentences and recommendations, 10 pt for lists), then cut at a word with "..." (the full text is in the Excel workbook).
- Radar axis labels are sized (12 pt, or 10 pt for eight axes) so they no longer wrap mid-word ("Governanc/e") or get cut with an ellipsis.
- Blank sections are cleaner: a value placeholder is cleared whole (no stray "TB", "N=", half sentences); the empty chart on the scope slide loses its legend.
- Our text carries no spelling/grammar marks (no underline under a word).
- New render check (tests/render_check.py): the deck is rendered and any text on top of text, or running into the right-hand stripe, fails the build; the template's own overlaps are measured first.

## 0.8.0 (the Ingram MAP deliverable is the default deck)
- The PowerPoint output is now filled into Ingram Micro's MAP deliverable template, as supplied: the same 25 slides, order, layouts, colours and fonts; nothing added, nothing removed
  (checked on every build). The MRA slides (recommendations, executive summary, areas of focus, readiness charts) are filled; the sections that need discovery, 7R or cost inputs stay in
  the deck, blank, with no sample data or `<>` markers. The red "Example" markers are removed. The AWS MRA readout format remains as the second built-in format.
- Text fitting engine: every text is fitted to its box (template font size kept when it fits, then shrunk, then cut with an ellipsis and reported); boxes that reach the layout's right-hand
  stripe are narrowed so text never runs into it. Hand-typed sample labels in the template's charts are replaced by real values (one decimal).
- Open items list the blank sections and what each needs; a `.potx` template is accepted; coverage reports name the format. Built-in ids are now `ingram` (default) and `aws`.

## 0.7.1
- `deploy.sh` no longer depends on the Python environment active in your shell: it makes `.venv-deploy`, installs the requirements there, checks the CDK
  library imports, and starts the CDK with that exact interpreter (quoted, so paths containing `&` or spaces work). Fixes `No module named 'aws_cdk'`.
- Removed the CloudFront minimum-TLS setting: it has no effect with the default certificate (CDK warned), so the docs no longer claim TLS 1.2.
- Examples use the username `mra-analyst` (any username is allowed).

## 0.7.0 (hosted on CloudFront)
- **Hosted version**: the review page on CloudFront (private S3 origin), its API behind API Gateway with a Cognito token, the same Lambda code. One CDK stack.
- **Sign-in**: a login screen with username and password (Cognito, **no MFA**, invite only, no OAuth), first-sign-in password change, tokens kept for the tab only
  and renewed automatically; a user without the reviewer group is read-only.
- **Large files never pass through the API**: uploads go to S3 with a presigned form (size-limited), downloads come through a short-lived signed link.
- **Background jobs** for scoring, agent tasks and rating changes (API Gateway allows 30 s); the page waits for them. One slow task per run; stale locks clear.
- A shared router now serves both the local page and the Lambda (one set of routes, errors and permissions).
- Strict Content-Security-Policy and browser protections on every response, from one definition used by the stack and the tests.
- `scripts/deploy.sh` (with `--dry-run`), `scripts/add_user.py`, `scripts/smoke_test.py` (checks a real deployment end to end).
- Tests: the Lambda handler with S3 semantics, the stack template, the page in a real browser against a stand-in for the AWS path, and the three scripts.
- Fixed while building: CDK's default Cognito client enabled OAuth flows with a dummy callback (now off); the site build put the page's files where the page does not
  look (it would have been blank); the preview is explicitly unavailable when hosted; the old v0.4-style API handler was removed.

## 0.6.1
- The page's server ignores a browser that drops the connection mid-download (a traceback was printed; the download was not affected).

## 0.6.0 (a built-in deck format, and your own)
- **Built-in default deck**: the AWS MRA readout format, taken from the reference engagement's deck and cleaned of everything customer-specific
  (answers, ratings, scores, names, dates; people's names in the file properties and comment authors). `export --pptx` and the page need no upload.
- **Your own deck**: upload an AWS-format deck (Settings) or pass `--pptx-template`; outputs come in that format. Each deck is checked against the default
  (`check-template`): the page and the open-items file say exactly which parts (charts, heatmap boxes, sentences, tables, Q&A rows) it can and cannot fill.
- A library of decks in the page: choose one per export, make one the default, remove one. The audit log records which template each file was made from.
- `make-template SOURCE --out FILE`: make a clean template from any filled deck. It scans every part of the package (slides, notes, chart data, embedded
  workbooks, properties) for removed text, for strings you forbid (the customer's name and answers) and for people's names, and refuses to write on any hit.
- Fixed while building: the default's observation cells showed "No action required." instead of staying blank.

## 0.5.2
- `python -m mra ui --pptx-template <AWS deck.pptx>` (or `MRA_PPTX_TEMPLATE`): the deck is loaded and remembered at launch, so the page needs no upload.
  The launch prints whether it looks like the AWS deck; a missing or wrong file stops the launch with a clear message. Also `--docx-template`.

## 0.5.1 (the local review page)
- `python -m mra ui`: a browser page on this computer to upload the questionnaire and notes, see scores, set blank ratings, decide actions, resolve items,
  review agent proposals (exact quote in context), release, create and download the Excel, Word and PowerPoint files, and preview them as PDF.
- Local only (127.0.0.1), per-launch token, Host and Origin checks, strict Content-Security-Policy, all text inserted as text.
- Standard library only: no new Python dependencies. Preview needs LibreOffice (optional).
- Tested over real HTTP and in a real browser (Chromium): whole review, planted HTML in answers and notes, downloads, template upload, preview.
- Found and fixed while testing: inline styles blocked by our own policy (bars would not draw), a stray "null" text node, forms losing typed text after an error,
  a crash on a negative Content-Length.

## 0.5.0 (the real AWS questionnaire)
Built from a real engagement's questionnaire, Word report, deck and Scores sheet.
- **Question bank**: the AWS MRA questionnaire (162 questions: 62 scored ratings in six perspectives and 35 categories, scope and
  activity questions, 9 optional modules gated by scope answers). Imported with `import-questions`; answers are never copied.
- **Scoring** replaced with the AWS tool's rules (plain mean per group, half-up display rounding, radar = 3-decimal mean x 20).
  Removed: respondents, median, conflict spread, coverage, the overall score, the guessed Forms readers.
- **Blank rating = Incomplete** (the AWS tool shows 0). Blocks confirmation and release. A reviewer or an accepted agent proposal sets it.
- **Second hierarchy**: 8 workstreams and 34 readiness activities (config/workstreams.json, inferred), with Pre-Mobilize / Mobilize phases.
- **Outputs in the AWS formats**: workbook (Questions, Actions, Scores identical in layout to the export), Word readout, PowerPoint
  deck filled from the AWS template (charts and their workbooks, heatmap, sentences, tables, Q&A appendix), open-items list.
- **Rule-based deck text**: areas-of-focus sentences, strengths, gaps and key observations, reproduced from the real deck.
- **Agent**: new `rating` task (propose a rating for a blank question with a verified quote; a person accepts).
- **Findings fixed**: F61 stale confirmation (confirmation now tied to a scores fingerprint; stale review records dropped and logged),
  F62 input hashes lost on recompute, F63 decimal ratings silently truncated, F64 spreadsheet formula injection, F66 hosted request
  overwrote input before refusing, F67 config thresholds and weights unchecked. Also: uploaded file names are kept in the hashes.
- **Tests**: rewritten for the new design, with a golden fixture whose expected values come from the AWS tool's own outputs.
- New dependencies: python-pptx, pillow.
