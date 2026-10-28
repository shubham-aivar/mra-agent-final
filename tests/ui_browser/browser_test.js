// Drives the real page in a real browser (Playwright + Chromium). Run by tests/test_ui_browser.py.
// env: MRA_URL, MRA_QUESTIONNAIRE, MRA_NOTES (file path), MRA_DECK (an AWS-style deck), MRA_OUT (folder), MRA_SHOTS (folder, optional)
const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const { chromium } = require(path.join(execSync("npm root -g").toString().trim(), "playwright"));

const E = process.env;
const XSS_NAME = 'Acme <img src=x onerror="window.__xss1=1"> & Co';
const results = [];
let page, shotN = 0;

async function step(name, fn) {
  try { await fn(); results.push([true, name]); console.log("PASS  " + name); }
  catch (e) { results.push([false, name]); console.log("FAIL  " + name + "\n      " + String(e.message).split("\n").slice(0, 4).join("\n      ")); if (E.MRA_SHOTS) await shot("FAIL-" + name.replace(/\W+/g, "-").slice(0, 40)); }
}
async function shot(label) {
  if (!E.MRA_SHOTS) return;
  fs.mkdirSync(E.MRA_SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(E.MRA_SHOTS, String(++shotN).padStart(2, "0") + "-" + label + ".png"), fullPage: true });
}
const eq = (a, b, msg) => { if (a !== b) throw new Error((msg || "expected") + ": got " + JSON.stringify(a) + ", wanted " + JSON.stringify(b)); };
const ok = (c, msg) => { if (!c) throw new Error(msg); };
const text = () => page.locator("main").innerText();
const idle = async () => { await page.waitForFunction(() => !document.querySelector(".overlay"), null, { timeout: 120000 }); };
const field = (label) => page.locator("label.f", { hasText: label }).first().locator("input,select,textarea").first();
const tab = async (name) => {
  await page.getByRole("link", { name: new RegExp("^" + name) }).click();
  await page.locator("nav.tabs a.on", { hasText: new RegExp("^" + name) }).waitFor();
  await idle();
};

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ acceptDownloads: true, viewport: { width: 1280, height: 900 } });
  page = await ctx.newPage();
  const problems = [];
  page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
  // The page deliberately provokes some 403/400 answers (no token, empty reason, ...); the browser logs those as console errors.
  // Anything else (a policy violation, a script error) is a real problem.
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource: the server responded with a status of 4\d\d/.test(m.text())) problems.push("console: " + m.text()); });
  page.on("dialog", (d) => { problems.push("dialog: " + d.message()); d.dismiss(); });

  await step("the page opens and says which text engine it uses", async () => {
    await page.goto(E.MRA_URL);
    await page.waitForSelector("text=New assessment");
    ok((await page.locator("header.top .badge").innerText()).includes("stub (offline, no AWS)"), "engine badge");
    ok((await text()).includes("No runs yet."), "empty runs list");
    ok(!/^null$/m.test(await page.locator("body").innerText()), "no stray 'null' text on the page");
    await shot("start");
  });

  await step("upload a questionnaire and notes; the customer name with HTML is shown as text", async () => {
    await page.locator('input[type=file]').first().setInputFiles(E.MRA_QUESTIONNAIRE);
    await page.locator('input[type=file][multiple]').setInputFiles(E.MRA_NOTES);
    await field("Customer name").fill(XSS_NAME);
    await field("Run id").fill("ui1");
    await field("Facilitator").fill("Aivar demo");
    await page.getByRole("button", { name: "Score it" }).click();
    await page.waitForSelector("h2 >> text=Acme");
    await idle();
    eq(await page.locator("main h2").first().innerText(), XSS_NAME, "customer heading is literal text");
    eq(await page.evaluate(() => window.__xss1), undefined, "customer-name script did not run");
    eq(await page.locator("main img").count(), 0, "no image element was created from the customer name");
    await shot("overview-with-blank");
  });

  await step("the blank rating is called out and People is Incomplete, never 0", async () => {
    const t = await text();
    ok(t.includes("1 rating(s) are blank: Q42"), "blank banner");
    const row = page.locator("tr", { hasText: /^People/ }).first();
    ok((await row.innerText()).includes("Incomplete"), "People shows Incomplete");
    ok(!/People\s+0(\.0)?\s/.test(t), "People is not shown as 0");
    ok((await page.locator("nav.tabs a", { hasText: "Ratings" }).innerText()).includes("1"), "ratings tab count");
  });

  await step("the Release tab refuses to confirm while a rating is blank", async () => {
    await tab("Release");
    ok(await page.getByRole("button", { name: "Confirm the scores" }).isDisabled(), "confirm disabled");
    ok((await text()).includes("rating(s) are blank (Q42)"), "blocker text");
  });

  await step("set the blank rating from the Ratings tab", async () => {
    await tab("Ratings");
    ok((await text()).includes("Blank ratings (1)"), "blank section");
    await page.locator(".levels label").first().click();                    // level 1
    await page.locator("textarea").first().fill("No cloud team (the answer to Q22)");
    await page.getByRole("button", { name: "Set this rating" }).click();
    await idle();
    ok((await text()).includes("Q42 set to 1"), "notice");
    ok((await text()).includes("Every scored question has a rating"), "no blanks left");
    await tab("Overview");
    ok(/People\s+2\.8/.test(await text()), "People now 2.8");
    eq(await page.locator(".chip.c-incomplete").count(), 0, "no group is shown as Incomplete any more");
    ok(!(await text()).includes("a rating is blank"), "no blank warning left");
    await shot("overview-complete");
  });

  await step("an empty reason is refused with a message", async () => {
    await tab("Ratings");
    await page.locator("details", { hasText: "Change" }).first().locator("summary").click();
    await page.locator("details[open] .levels label").nth(2).click();
    await page.locator("details[open]").getByRole("button", { name: "Change this rating" }).click();
    await idle();
    ok((await page.locator(".banner.err").innerText()).includes("written reason"), "reason error shown");
  });

  await step("decide on actions in the page, then the rest through the same API", async () => {
    await tab("Activities");
    const first = page.locator(".card", { has: page.locator("summary", { hasText: "Decide" }) }).first();   // pending ones are open already
    await first.locator("label.f", { hasText: "Owner" }).locator("input").fill("Customer lead");
    await first.locator("label.f", { hasText: "Due date" }).locator("input").fill("2026-12-15");
    await first.getByRole("button", { name: "Save decision" }).click();
    await idle();
    ok((await text()).includes("Decision saved"), "decision saved notice");
    await page.evaluate(async () => {
      const v = await api("GET", "/api/runs/ui1");
      for (const a of v.activities) if (a.decision === "pending") await api("POST", "/api/runs/ui1/decide", { activity: a.id, decision: "approve", owner: "Customer lead", due_date: "2026-12-15" });
    });
    await reloadView();
    await shot("activities");
  });

  await step("a rejection without a comment is refused", async () => {
    await tab("Activities");
    const card = page.locator(".card", { has: page.locator("summary", { hasText: "Decide" }) }).first();
    const det = card.locator("details").first();
    if (!(await det.evaluate((n) => n.open))) await det.locator("summary").click();                    // already decided ones start closed
    await card.locator("select").selectOption("reject");
    await card.locator("label.f", { hasText: "Owner" }).locator("input").fill("Keep me");
    await card.locator("label.f", { hasText: "Comment" }).locator("input").fill("");
    await card.getByRole("button", { name: "Save decision" }).click();
    await idle();
    ok((await page.locator(".banner.err").innerText()).includes("needs a comment"), "reject error");
    eq(await card.locator("label.f", { hasText: "Owner" }).locator("input").inputValue(), "Keep me", "what was typed survives the error");
    ok(await card.locator("details").first().evaluate((n) => n.open), "the form stays open after the error");
    await tab("Validation");
    eq(await page.locator(".banner").count(), 0, "an error does not follow you to another tab");
  });

  await step("resolve a validation item in the page, then the rest", async () => {
    await tab("Validation");
    const card = page.locator(".card").filter({ has: page.getByRole("button", { name: "Save" }) }).first();
    await card.locator("input[type=text]").fill("seen in the workshop");
    await card.getByRole("button", { name: "Save" }).click();
    await idle();
    ok((await text()).includes("resolved."), "resolved notice");
    await page.evaluate(async () => {
      const v = await api("GET", "/api/runs/ui1");
      for (const i of v.validation) if (i.resolution === "open") await api("POST", "/api/runs/ui1/resolve", { item: i.key, resolution: "accepted_risk", note: "reviewed" });
    });
    await reloadView();
  });

  await step("the agent runs from the page, shows a quote in context, and a person accepts", async () => {
    await tab("Proposals");
    await page.locator("select").first().selectOption("context");
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await idle();
    ok((await text()).includes("Agent task 'context' stopped because done"), "task notice");
    ok((await page.locator(".quote mark").count()) > 0, "the quote is highlighted inside its note");
    const before = await page.locator("nav.tabs a", { hasText: "Proposals" }).innerText();
    await page.getByRole("button", { name: "Accept", exact: true }).first().click();
    await idle();
    ok((await text()).includes("accepted."), "accepted notice");
    ok((await page.locator("nav.tabs a", { hasText: "Proposals" }).innerText()) !== before, "pending count went down");
    await shot("proposals");
  });

  await step("answers and notes are shown as text; planted HTML does not run", async () => {
    await tab("Answers and notes");
    const t = await text();
    ok(t.includes('<img src=x onerror="window.__xss3=1">'), "the answer is displayed literally");
    await page.locator("summary", { hasText: "E-" }).first().click();
    ok((await page.locator("pre").first().innerText()).includes("<script>window.__xss2=1</script>"), "the note shows the script as text");
    eq(await page.evaluate(() => [window.__xss2, window.__xss3]).then((a) => a.join(",")), ",", "no planted script ran");
    eq(await page.locator("main img, main script").count(), 0, "no img or script element inside the page body");
  });

  await step("release: details, confirm, finalise", async () => {
    await tab("Release");
    await field("Workshop date").fill("2026-10-23");
    await field("Readout date").fill("2026-10-30");
    await page.getByRole("button", { name: "Save details" }).click();
    await idle();
    await page.getByRole("button", { name: "Confirm the scores" }).click();
    await idle();
    ok((await text()).includes("Scores confirmed: yes"), "confirmed");
    await page.getByRole("button", { name: "Finalise the report" }).click();
    await idle();
    ok((await text()).includes("Finalised by"), "finalised banner");
    ok(await page.getByRole("button", { name: "Save details" }).isDisabled(), "details locked");
    await shot("final");
  });

  await step("a changed rating after finalising is refused", async () => {
    const r = await page.evaluate(async () => { try { await api("POST", "/api/runs/ui1/adjust", { question: "Q26", score: 2, reason: "x" }); return "allowed"; } catch (e) { return e.status + " " + e.message; } });
    ok(r.startsWith("409"), "locked: " + r);
  });

  await step("create the Excel and Word files and download one", async () => {
    await tab("Outputs");
    await page.locator("label.row", { hasText: "PowerPoint" }).locator("input").uncheck();
    await page.getByRole("button", { name: "Create files" }).click();
    await idle();
    ok((await text()).includes("MRA_ui1_FINAL.xlsx") && (await text()).includes("MRA_ui1_FINAL.docx"), "files listed");
    const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "MRA_ui1_FINAL.xlsx" }).click()]);
    eq(dl.suggestedFilename(), "MRA_ui1_FINAL.xlsx", "download name");
    const p = path.join(E.MRA_OUT, "dl.xlsx"); await dl.saveAs(p);
    const buf = fs.readFileSync(p);
    ok(buf.length > 5000 && buf.slice(0, 2).toString() === "PK", "a real xlsx was downloaded");
    await shot("outputs");
  });

  await step("the deck is created from the built-in default with no upload", async () => {
    await page.locator("label.row", { hasText: "PowerPoint" }).locator("input").check();
    ok((await page.locator("#deck-format option").allInnerTexts()).join("|") === "Ingram MAP deliverable (built in)|AWS MRA readout format (built in)", "the two built-in formats are offered");
    ok((await text()).includes("MRA slides are filled; sections that need other inputs"), "the Ingram deliverable is described");
    await page.getByRole("button", { name: "Create files" }).click();
    await page.locator(".banner.ok", { hasText: "Deck format used: Ingram MAP deliverable (built in)." }).waitFor();
    await idle();
    ok((await text()).includes("MRA_ui1_FINAL.pptx") && (await text()).includes("open_items.md"), "deck and open-items files listed");
    await shot("default-deck");
  });

  await step("Settings lists the built-in default and checks an uploaded deck against it", async () => {
    await page.getByRole("link", { name: "Settings" }).click();
    await page.locator("h3", { hasText: "Deck format (PowerPoint)" }).waitFor();
    ok((await text()).includes("always available") && (await text()).includes("used by default"), "the built-in default is listed as the default");
    ok((await page.getByRole("button", { name: "Remove" }).count()) === 0, "the built-in default cannot be removed");
    await page.locator('input[type=file][accept=".pptx"]').setInputFiles(E.MRA_DECK);
    await page.locator("label.row", { hasText: "Use it by default" }).locator("input").uncheck();
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await page.locator(".banner.ok", { hasText: "Added AWS deck.pptx." }).waitFor();
    await idle();
    const banner = await page.locator(".banner.ok").innerText();
    if (E.MRA_REAL_DECK === "1") ok(banner.includes("same format as the AWS MRA readout format: every part can be filled"), "the real AWS deck matches in full: " + banner);
    else ok(banner.includes("Only part of it can be filled"), "a deck with only some parts is flagged: " + banner);
    const card = page.locator(".card", { hasText: "AWS deck.pptx" }).filter({ has: page.getByRole("button", { name: "Remove" }) });
    await card.locator("summary", { hasText: "What can be filled" }).click();
    ok((await card.innerText()).includes("Charts (scores and radars)"), "the coverage table is shown");
    ok((await card.innerText()).includes("not the default"), "it was not made the default");
    await shot("settings");
  });

  await step("the uploaded deck can be chosen for an export, and the choice is recorded", async () => {
    await page.evaluate(() => { location.hash = "#/run/ui1/outputs"; });
    await page.locator("#deck-format").waitFor();
    eq((await page.locator("#deck-format option").count()), 3, "three formats are offered");
    await page.locator("#deck-format").selectOption({ label: "AWS deck.pptx" });
    const note = await page.locator("main").innerText();
    if (E.MRA_REAL_DECK === "1") ok(note.includes("Same format as the AWS MRA readout format"), "its coverage is shown"); else ok(note.includes("Only part of this deck can be filled"), "its gaps are shown");
    await page.getByRole("button", { name: "Create files" }).click();
    await page.locator(".banner.ok", { hasText: "Deck format used: AWS deck.pptx." }).waitFor();
    await idle();
    const audit = await page.evaluate(async () => (await api("GET", "/api/runs/ui1")).audit.entries);
    ok(audit > 0, "audit log present");
  });

  await step("an uploaded deck can be made the default and removed again", async () => {
    await page.getByRole("link", { name: "Settings" }).click();
    await page.locator("h3", { hasText: "Deck format (PowerPoint)" }).waitFor();
    await page.locator(".card", { hasText: "AWS deck.pptx" }).filter({ has: page.getByRole("button", { name: "Remove" }) }).getByRole("button", { name: "Use this by default" }).click();
    await page.locator(".banner.ok", { hasText: "Now using: AWS deck.pptx" }).waitFor();
    await idle();
    await page.evaluate(() => { location.hash = "#/run/ui1/outputs"; });
    await page.locator("#deck-format").waitFor();
    eq(await page.locator("#deck-format").inputValue() === (await page.locator("#deck-format option", { hasText: "AWS deck.pptx" }).getAttribute("value")), true, "the new default is preselected");
    await page.getByRole("link", { name: "Settings" }).click();
    await page.locator("h3", { hasText: "Deck format (PowerPoint)" }).waitFor();
    await page.getByRole("button", { name: "Remove" }).click();
    await page.locator(".banner.ok", { hasText: "Removed AWS deck.pptx." }).waitFor();
    await idle();
    ok((await page.getByRole("button", { name: "Remove" }).count()) === 0, "only the built-in default is left");
    ok((await text()).includes("used by default"), "and it is the default again");
  });

  if (E.MRA_EXPECT_PREVIEW === "1") await step("the preview shows the report as a PDF in the page", async () => {
    await page.evaluate(() => { location.hash = "#/run/ui1/outputs"; });
    await page.locator("#deck-format").waitFor();
    await page.getByRole("button", { name: "Preview the report" }).click();
    await idle();
    const src = await page.locator("iframe.preview").getAttribute("src");
    ok(src && src.startsWith("/api/runs/ui1/preview/docx.pdf?t="), "iframe points at the preview");
    const r = await page.evaluate(async (u) => {
      const x = await fetch(u); const b = new Uint8Array(await x.arrayBuffer()); let bin = "";
      for (let i = 0; i < b.length; i += 8192) bin += String.fromCharCode(...b.subarray(i, i + 8192));
      return [x.status, x.headers.get("content-type"), b.length, btoa(bin)];
    }, src);
    eq(r[0], 200); eq(r[1], "application/pdf"); ok(r[2] > 1000, "pdf has content");
    if (E.MRA_SHOTS) fs.writeFileSync(path.join(E.MRA_SHOTS, "preview.pdf"), Buffer.from(r[3], "base64"));
    ok((await page.locator("a", { hasText: "Open the preview in its own tab" }).getAttribute("target")) === "_blank", "new-tab link");
    await shot("preview");
    await page.getByRole("button", { name: "Preview the deck" }).click();
    await idle();
    const deckSrc = await page.locator("iframe.preview").getAttribute("src");
    ok(deckSrc.startsWith("/api/runs/ui1/preview/pptx.pdf?t="), "the deck preview works with no upload");
    const d = await page.evaluate(async (u) => { const x = await fetch(u); const b = new Uint8Array(await x.arrayBuffer()); return [x.status, String.fromCharCode(...b.subarray(0, 5)), b.length]; }, deckSrc);
    eq(d[0], 200); eq(d[1], "%PDF-"); ok(d[2] > 10000, "the deck pdf has content");
  });

  await step("the browser cannot call the API without the token, and the run list shows the run", async () => {
    const r = await page.evaluate(async () => (await fetch("/api/runs")).status);
    eq(r, 403, "no token");
    await page.getByRole("link", { name: "A04 MRA Agent" }).click();
    await page.locator("table tr", { hasText: "ui1" }).waitFor();
    ok((await page.locator("table tr", { hasText: "ui1" }).innerText()).includes("FINAL"), "run listed as FINAL");
    eq(await page.locator(".banner").count(), 0, "the earlier notice is gone");
  });

  await step("deleting a run needs its id typed", async () => {
    await page.evaluate(() => { location.hash = "#/run/ui1/release"; });
    await page.waitForSelector("text=Delete this run");
    await page.locator("summary", { hasText: "Delete this run" }).click();
    await page.locator("details[open] input[type=text]").fill("wrong");
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await idle();
    ok((await page.locator(".banner.err").innerText()).includes("type the run id"), "refused");
    await page.locator("details[open] input[type=text]").fill("ui1");
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await idle();
    await page.waitForSelector("text=No runs yet.");
  });

  await step("no script errors, console errors or dialogs happened", async () => { eq(problems.join(" | "), "", "problems"); });

  await browser.close();
  const failed = results.filter((r) => !r[0]).length;
  console.log(`\n${results.length - failed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);

  async function reloadView() { await page.evaluate(() => { return loadRun("ui1").then(() => render()); }); await idle(); }
})();
