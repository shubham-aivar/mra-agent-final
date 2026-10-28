# The local review page

    python3 -m mra ui                  # opens http://127.0.0.1:8765 in your browser
    python3 -m mra ui --llm bedrock    # the same page, with the real model for the text (uses AWS; the badge in the header says so)
    python3 -m mra ui --pptx-template "/path/to/your deck.pptx"   # use your own AWS-format deck by default (the built-in one needs no flag)
    python3 -m mra ui --port 8800 --no-browser

It is for one person on one laptop. Data stays where `config/settings.json` says (`.local/` by default; S3 only if you set it).

## What you can do
1. **Runs**: upload the filled-in questionnaire and the interview notes (.txt/.md), give the report details, press *Score it*.
   Uploading again with the same run id replaces its answers and notes (stale decisions are dropped and logged); a finalised run must be reopened first.
2. **Overview**: scores per perspective and workstream. A blank rating shows **Incomplete**, never 0. The sentences and lists the deck will carry are shown here.
3. **Ratings**: give a blank question a rating (read the five level descriptions), or change one. A reason is required and is audited.
4. **Activities**: the observation and proposed actions per readiness activity; approve, edit or reject with an owner and due date.
5. **Validation**: resolve each item with a note (a blank rating is fixed on the Ratings tab, not with a note).
6. **Proposals**: run an agent task (rating, evidence, investigate, context); each proposal shows its exact quote highlighted in the note. You accept or reject.
7. **Answers and notes**: everything the page received, shown as text.
8. **Release**: report details, *Confirm the scores*, *Finalise*, *Reopen* (with a reason).
9. **Outputs**: create the Excel, Word, PowerPoint and planning-note files (pick the deck format per export), download them, preview the deck or report as a PDF.
10. **Settings**: the deck-format library: the two built-in formats (Ingram MAP deliverable, the default, and the AWS MRA readout) and any decks you add, each with a table of what the agent can fill in it. Choose the default, remove one, or add a Word template. See `docs/TEMPLATES.md`. The page checks that it looks like the AWS deck (named charts, the heatmap slide).

## Safety (it handles customer material)
- Listens on 127.0.0.1 only. Each launch has a random token; every request must carry it. The Host header must be this address and a browser's Origin, if sent, too.
- No inline script or style (strict Content-Security-Policy); everything shown is inserted as text, so HTML in an answer or note appears as text and never runs.
- Only fixed files and the run's own export names are read; request bodies are size-limited; one lock serialises actions.
- The page never calculates a score, colour or sentence: it shows what the service returns.

## Preview needs LibreOffice
`brew install --cask libreoffice`. Without it the page still works; download the files and open them in PowerPoint or Word.
Set `MRA_SOFFICE=/path/to/soffice` if it is somewhere unusual.

## Tests
`tests/test_ui_server.py` (real HTTP, security guards, whole flow), `tests/test_ui_api.py`, and `tests/test_ui_browser.py`, which drives the real page in
Chromium (needs `npm install -g playwright && npx playwright install chromium`; skipped otherwise).
Optional: `MRA_TEMPLATE_DECK=<AWS deck.pptx>`, `MRA_BROWSER_TEST_PREVIEW=1`, `MRA_SHOTS=<folder>` for screenshots.

## Not built
Sign-in and multiple users (this is the local page; a hosted version also needs the Cognito sign-in page), drag-and-drop of a notes folder, editing the
question bank, viewing slides as images without LibreOffice.
