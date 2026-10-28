// Drives the HOSTED page in a real browser against the stand-in AWS path (tests/hosted_harness.py).
// env: MRA_URL, MRA_S3_PORT, MRA_QUESTIONNAIRE, MRA_NOTES, MRA_DECK, MRA_OUT, MRA_SHOTS (optional)
const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const { chromium } = require(path.join(execSync("npm root -g").toString().trim(), "playwright"));

const E = process.env;
const EDGE = new URL(E.MRA_URL).host;
const MOTO = "127.0.0.1:" + E.MRA_S3_PORT;
const XSS_NAME = 'Acme <img src=x onerror="window.__xss1=1"> & Co';
const results = [];
let page, shotN = 0;
const reqs = [];

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
const idle = () => page.waitForFunction(() => !document.querySelector(".overlay"), null, { timeout: 120000 });
const tab = async (name) => { await page.getByRole("link", { name: new RegExp("^" + name) }).click(); await page.locator("nav.tabs a.on", { hasText: new RegExp("^" + name) }).waitFor(); await idle(); };
const refreshCalls = () => reqs.filter((r) => r.header("x-amz-target") === "AWSCognitoIdentityProviderService.InitiateAuth" && (r.post || "").includes("REFRESH_TOKEN_AUTH")).length;

async function signIn(user, password, viaEnter) {
  await page.locator("#username").fill(user);
  await page.locator("#password").fill(password);
  if (viaEnter) await page.locator("#password").press("Enter"); else await page.locator("#signin").click();
}
async function signedIn(name) { await page.locator("#whoami", { hasText: "Signed in as " + name }).waitFor({ timeout: 30000 }); await idle(); }

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ acceptDownloads: true, viewport: { width: 1280, height: 900 } });
  page = await ctx.newPage();
  const problems = [];
  page.on("request", (r) => reqs.push({ url: r.url(), method: r.method(), post: r.postData(), header: (n) => r.headers()[n], headers: r.headers() }));
  page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource: the server responded with a status of 4\d\d/.test(m.text())) problems.push("console: " + m.text()); });
  page.on("dialog", (d) => { problems.push("dialog: " + d.message()); d.dismiss(); });

  await step("the hosted page asks for sign-in first and makes no API call before it", async () => {
    await page.goto(E.MRA_URL);
    await page.locator("#signin").waitFor();
    ok((await text()).includes("Sign in"), "sign-in heading");
    eq(await page.locator("#actor").count(), 0, "no 'Your name' box when hosted");
    eq(await page.getByRole("link", { name: "Settings" }).count(), 0, "no Settings link before sign-in");
    eq(reqs.filter((r) => new URL(r.url).pathname.startsWith("/api/")).length, 0, "no API call before sign-in");
    await shot("login");
  });

  await step("a wrong password or empty fields give a plain message", async () => {
    await page.locator("#signin").click();
    ok((await page.locator(".banner.err").innerText()).includes("Enter your username and password."), "empty fields");
    await signIn("demo", "not-the-password");
    await page.locator(".banner.err", { hasText: "Incorrect username or password." }).waitFor();
    await signIn("nobody", "not-the-password");
    await page.locator(".banner.err", { hasText: "Incorrect username or password." }).waitFor();                // the same message: no hint which part is wrong
  });

  await step("a first sign-in must choose a new password", async () => {
    await signIn("fresh", "Temp#12345");
    await page.locator("#save-password").waitFor();
    ok((await text()).includes("Choose a new password"), "new password form");
    await page.locator("#new1").fill("Newer#Pass1234"); await page.locator("#new2").fill("different");
    await page.locator("#save-password").click();
    ok((await page.locator(".banner.err").innerText()).includes("not the same"), "mismatch refused");
    await page.locator("#new1").fill("short"); await page.locator("#new2").fill("short");
    await page.locator("#save-password").click();
    ok((await page.locator(".banner.err").innerText()).includes("at least 10 characters"), "short refused");
    await page.locator("#new1").fill("Newer#Pass1234"); await page.locator("#new2").fill("Newer#Pass1234");
    await page.locator("#save-password").click();
    await signedIn("fresh");
  });

  await step("signing out clears the session; signing in with Enter works and keeps tokens out of local storage", async () => {
    await page.locator("#signout").click();
    await page.locator("#signin").waitFor();
    eq(await page.evaluate(() => sessionStorage.getItem("mra-auth")), null, "session cleared");
    await signIn("demo", "Demo#Pass12345", true);
    await signedIn("demo");
    ok(await page.evaluate(() => !!sessionStorage.getItem("mra-auth")), "session kept for this tab");
    eq(await page.evaluate(() => Object.keys(localStorage).filter((k) => /auth|token/i.test(k)).length), 0, "no tokens in local storage");
    eq(await page.evaluate(() => location.href.includes("id_token") || location.hash.includes("token")), false, "no token in the address");
  });

  await step("files go straight to storage with no credentials, and the run is made by a background job", async () => {
    const before = reqs.length;
    await page.locator('input[type=file]').first().setInputFiles(E.MRA_QUESTIONNAIRE);
    await page.locator('input[type=file][multiple]').setInputFiles(E.MRA_NOTES);
    await page.locator("label.f", { hasText: "Customer name" }).locator("input").fill(XSS_NAME);
    await page.locator("label.f", { hasText: "Run id" }).locator("input").fill("ui1");
    await page.locator("label.f", { hasText: "Facilitator" }).locator("input").fill("Aivar demo");
    await page.getByRole("button", { name: "Score it" }).click();
    await page.waitForSelector("h2 >> text=Acme", { timeout: 120000 });
    await idle();
    const mine = reqs.slice(before);
    const s3 = mine.filter((r) => new URL(r.url).host === MOTO && r.method === "POST" && new URL(r.url).pathname.startsWith("/mra-harness-data"));
    eq(s3.length, 2, "two files uploaded to storage (the questionnaire and one note)");
    ok(s3.every((r) => !r.headers["authorization"] && !r.headers["x-mra-token"]), "no credentials sent to the storage host");
    const api = mine.filter((r) => new URL(r.url).host === EDGE && new URL(r.url).pathname.startsWith("/api/"));
    ok(api.filter((r) => r.url.endsWith("/api/uploads")).length === 2, "two upload sessions");
    ok(api.some((r) => r.url.endsWith("/api/runs") && r.method === "POST"), "the run was requested");
    ok(api.some((r) => /\/api\/jobs\/[0-9a-f]{32}$/.test(r.url)), "and its job was polled");
    ok(api.every((r) => (r.headers["authorization"] || "").startsWith("Bearer ")), "every API call carries the token");
    eq(await page.locator("main h2").first().innerText(), XSS_NAME, "customer name is literal text");
    eq(await page.evaluate(() => window.__xss1), undefined, "planted script did not run");
    await shot("overview");
  });

  await step("the blank rating is called out; setting it runs as a job and People is no longer Incomplete", async () => {
    ok((await text()).includes("1 rating(s) are blank: Q42"), "blank banner");
    ok((await page.locator("tr", { hasText: /^People/ }).first().innerText()).includes("Incomplete"), "People Incomplete");
    await tab("Ratings");
    await page.locator(".levels label").first().click();
    await page.locator("textarea").first().fill("No cloud team (the answer to Q22)");
    await page.getByRole("button", { name: "Set this rating" }).click();
    await idle();
    ok((await text()).includes("Q42 set to 1"), "notice");
    await tab("Overview");
    ok(/People\s+2\.8/.test(await text()), "People 2.8");
  });

  await step("an agent task runs as a job and its proposal can be accepted", async () => {
    await tab("Proposals");
    await page.locator("select").first().selectOption("context");
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await idle();
    ok((await text()).includes("Agent task 'context' stopped because done"), "task result shown");
    ok((await page.locator(".quote mark").count()) > 0, "the quote is shown in its note");
    await page.getByRole("button", { name: "Accept", exact: true }).first().click();
    await idle();
    ok((await text()).includes("accepted."), "accepted");
  });

  await step("a failed job shows its real error and the run stays usable", async () => {
    const r = await page.evaluate(async () => { try { await api("POST", "/api/runs/ui1/adjust", { question: "Q9999", score: 1, reason: "x" }); return "allowed"; } catch (e) { return e.status + "|" + e.message; } });
    ok(/^(400|404)\|.+/.test(r), "the job's error is raised with its status: " + r);
    eq(await page.evaluate(async () => (await api("GET", "/api/runs/ui1")).customer.length > 0), true, "the run still loads");
  });

  await step("decide, resolve, confirm and finalise", async () => {
    await page.evaluate(async () => {
      const v = await api("GET", "/api/runs/ui1");
      for (const a of v.activities) if (a.decision === "pending") await api("POST", "/api/runs/ui1/decide", { activity: a.id, decision: "approve", owner: "Customer lead", due_date: "2026-12-15" });
      for (const i of v.validation) if (i.resolution === "open") await api("POST", "/api/runs/ui1/resolve", { item: i.key, resolution: "accepted_risk", note: "reviewed" });
    });
    await page.evaluate(() => loadRun("ui1").then(() => render()));
    await tab("Release");
    await page.locator("label.f", { hasText: "Workshop date" }).locator("input").fill("2026-10-23");
    await page.locator("label.f", { hasText: "Readout date" }).locator("input").fill("2026-10-30");
    await page.getByRole("button", { name: "Save details" }).click(); await idle();
    await page.getByRole("button", { name: "Confirm the scores" }).click(); await idle();
    await page.getByRole("button", { name: "Finalise the report" }).click(); await idle();
    ok((await text()).includes("Finalised by demo"), "finalised by the signed-in user (from the token, not typed)");
  });

  await step("files are created and downloaded from storage through a short-lived link", async () => {
    await tab("Outputs");
    await page.getByRole("button", { name: "Create files" }).click();
    await page.locator(".banner.ok", { hasText: "Files created." }).waitFor();
    ok((await text()).includes("MRA_ui1_FINAL.xlsx") && (await text()).includes("MRA_ui1_FINAL.pptx"), "files listed");
    ok((await text()).includes("not available in the hosted version"), "the preview is explained as not available");
    const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "MRA_ui1_FINAL.xlsx" }).click()]);
    ok(dl.url().includes("X-Amz-Signature") && new URL(dl.url()).host === MOTO, "the download came from storage with a signed link: " + dl.url().slice(0, 80));
    eq(dl.suggestedFilename(), "MRA_ui1_FINAL.xlsx", "file name");
    const p = path.join(E.MRA_OUT, "dl.xlsx"); await dl.saveAs(p);
    const buf = fs.readFileSync(p);
    ok(buf.length > 5000 && buf.slice(0, 2).toString() === "PK", "a real xlsx");
    const deck = await page.evaluate(async () => (await api("GET", "/api/runs/ui1")).exports.find((e) => e.name.endsWith(".pptx")).size);
    ok(deck > 1000000, "the deck is a multi-megabyte file, handed over without passing through the API: " + deck);
    await shot("outputs");
  });

  await step("a large deck template goes straight to storage and is checked", async () => {
    await page.getByRole("link", { name: "Settings" }).click();
    await page.locator("h3", { hasText: "Deck format (PowerPoint)" }).waitFor();
    await page.locator('input[type=file][accept=".pptx"]').setInputFiles(E.MRA_DECK);
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await page.locator(".banner.ok", { hasText: "Added AWS deck.pptx." }).waitFor({ timeout: 120000 });
    ok((await page.locator(".banner.ok").innerText()).includes("same format as the AWS MRA readout format"), "its coverage was checked");
    await shot("settings");
  });

  await step("a reader sees everything but can change nothing, and can still download", async () => {
    await page.locator("#signout").click();
    await page.locator("#signin").waitFor();
    await signIn("reader", "Reader#Pass12345");
    await signedIn("reader (read only)");
    await page.evaluate(() => { location.hash = "#/run/ui1/outputs"; });
    await page.locator("#readonly").waitFor();
    await page.getByRole("button", { name: "Create files" }).click();
    await page.locator(".banner.err", { hasText: "can read but not change anything" }).waitFor();
    const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "MRA_ui1_FINAL.xlsx" }).click()]);
    eq(dl.suggestedFilename(), "MRA_ui1_FINAL.xlsx", "a reader can download a finished file");
    await shot("reader");
  });

  await step("the page refreshes an expiring token by itself", async () => {
    await page.locator("#signout").click();
    await signIn("demo", "Demo#Pass12345");
    await signedIn("demo");
    const before = refreshCalls();
    await page.evaluate(() => { auth.exp = Date.now() + 5000; sessionStorage.setItem("mra-auth", JSON.stringify(auth)); });
    await page.getByRole("button", { name: "Refresh" }).click().catch(() => {});
    await page.evaluate(() => api("GET", "/api/runs"));
    eq(refreshCalls(), before + 1, "one refresh call to Cognito");
    ok(await page.evaluate(() => auth.exp - Date.now() > 600000), "a fresh token was stored");
    await signedIn("demo");
  });

  await step("an API refusal (401) triggers one refresh and a retry", async () => {
    const before = refreshCalls();
    await page.evaluate(() => fetch("/_test/reject-next", { method: "POST" }));
    const r = await page.evaluate(async () => (await api("GET", "/api/runs")).runs.length);
    ok(r >= 1, "the retry succeeded");
    eq(refreshCalls(), before + 1, "exactly one refresh");
  });

  await step("a session that cannot be refreshed returns to the sign-in page with a message", async () => {
    await page.evaluate(() => { auth.refresh = "invalid"; auth.exp = Date.now() - 1000; sessionStorage.setItem("mra-auth", JSON.stringify(auth)); });
    const r = await page.evaluate(async () => { try { await api("GET", "/api/runs"); return "allowed"; } catch (e) { return e.message; } });
    ok(r.includes("session has ended"), "the call fails clearly: " + r);
    await page.locator("#signin").waitFor();
    ok((await page.locator(".banner.err").innerText()).includes("sign in again"), "the page says so");
    eq(await page.evaluate(() => sessionStorage.getItem("mra-auth")), null, "nothing is kept");
  });

  await step("the page talks only to its own address, the sign-in service and the data bucket", async () => {
    const hosts = new Set(reqs.map((r) => new URL(r.url).host));
    ok([...hosts].every((h) => h === EDGE || h === MOTO), "hosts: " + [...hosts].join(", "));
    ok(reqs.filter((r) => new URL(r.url).pathname.startsWith("/api/")).every((r) => new URL(r.url).host === EDGE), "the API is only called on the page's own address");
    const idp = reqs.filter((r) => new URL(r.url).host === MOTO && new URL(r.url).pathname === "/" && r.method === "POST");
    ok(idp.length > 5 && idp.every((r) => (r.header("x-amz-target") || "").startsWith("AWSCognitoIdentityProviderService.")), "sign-in calls are Cognito calls");
  });

  await step("no script errors, policy violations or dialogs happened", async () => { eq(problems.join(" | "), "", "problems"); });

  await browser.close();
  const failed = results.filter((r) => !r[0]).length;
  console.log(`\n${results.length - failed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
