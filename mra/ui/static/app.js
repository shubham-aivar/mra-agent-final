"use strict";
/* A04 MRA Agent: local review page. It shows what the service returns and sends the reviewer's decisions back.
   It never calculates a score, a colour or a sentence. All text is inserted as text (never as HTML). */

const TOKEN = (document.querySelector('meta[name="mra-token"]') || { content: "" }).content;
let CONFIG = null;          // set when the page is served by CloudFront (config.json exists): sign-in, uploads and downloads go through AWS
let auth = null;            // the signed-in session (hosted only): { id, refresh, exp, name, groups }
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const state = { info: null, view: null, runs: null, actor: localStorage.getItem("mra-actor") || "", error: null, notice: null, busy: null };

// ---- small helpers ---------------------------------------------------------------------------------
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);   // DOM assignment is allowed by the strict policy; style attributes are not
    else if (k === "value") e.value = v;
    else if (k === "checked" || k === "disabled" || k === "selected" || k === "open") e[k] = Boolean(v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return e;
}
const $ = (sel, root) => (root || document).querySelector(sel);

function authHeaders() {
  return CONFIG ? { Authorization: "Bearer " + auth.id } : { "X-MRA-Token": TOKEN, "X-MRA-Actor": state.actor };
}

async function api(method, path, body) {
  if (CONFIG) await ensureToken();
  const send = () => {
    const headers = authHeaders();
    const opts = { method, headers };
    if (body !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    return fetch(path, opts);
  };
  let r = await send();
  if (r.status === 401 && CONFIG) {                                         // the token may have just expired: refresh once, then try again
    try { await refreshSession(); r = await send(); } catch (e) { signOut("Your session has ended: sign in again."); throw new Error("Your session has ended: sign in again."); }
    if (r.status === 401) { signOut("Your session has ended: sign in again."); throw new Error("Your session has ended: sign in again."); }
  }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : null;
  if (!r.ok) {
    const msg = data && (data.error || (data.errors && data.errors.join("; "))) || r.statusText;
    throw Object.assign(new Error(msg), { status: r.status, data });
  }
  if (r.status === 202 && data && data.job_id) return pollJob(data.job_id);   // a slow task was queued: wait for it, then answer as if it was immediate
  return data;
}

async function pollJob(jobId) {
  const started = Date.now();
  let delay = 1000;
  for (;;) {
    await sleep(delay);
    delay = Math.min(delay * 1.4, 4000);
    const j = await api("GET", "/api/jobs/" + jobId);
    setBusyNote("still working: " + Math.round((Date.now() - started) / 1000) + " s");
    if (j.state === "done") return j.result;
    if (j.state === "failed") {
      const d = j.payload || {};
      throw Object.assign(new Error(d.error || (d.errors && d.errors.join("; ")) || "the task failed"), { status: j.status, data: d });
    }
    if (Date.now() - started > 16 * 60 * 1000) throw new Error("The task is taking too long. Check the run again in a few minutes.");
  }
}

// ---- hosted sign-in (Cognito, username and password) ----------------------------------------------------------------------------------
async function cognito(target, payload) {
  const url = CONFIG.cognitoEndpoint || "https://cognito-idp." + CONFIG.region + ".amazonaws.com/";
  const r = await fetch(url, { method: "POST", body: JSON.stringify(payload),
    headers: { "Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": "AWSCognitoIdentityProviderService." + target } });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const kind = String(data.__type || "").split("#").pop();
    const friendly = { NotAuthorizedException: "Incorrect username or password.", UserNotFoundException: "Incorrect username or password.",
      PasswordResetRequiredException: "This password must be reset: ask the administrator.", UserNotConfirmedException: "This account is not ready yet: ask the administrator.",
      TooManyRequestsException: "Too many attempts. Wait a few minutes and try again.", LimitExceededException: "Too many attempts. Wait a few minutes and try again." }[kind];
    throw Object.assign(new Error(friendly || data.message || "Sign-in failed."), { kind });
  }
  return data;
}
function tokenClaims(jwt) {
  const part = jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(decodeURIComponent(atob(part).split("").map((c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0")).join("")));
}
function keepSession(result, previous) {
  const claims = tokenClaims(result.IdToken);
  auth = { id: result.IdToken, refresh: result.RefreshToken || (previous && previous.refresh), exp: claims.exp * 1000,
    name: claims.email || claims["cognito:username"] || claims.sub, groups: claims["cognito:groups"] || [] };
  sessionStorage.setItem("mra-auth", JSON.stringify(auth));       // this tab only: closing the tab signs out
}
function restoreSession() {
  try { auth = JSON.parse(sessionStorage.getItem("mra-auth") || "null"); } catch (e) { auth = null; }
  if (auth && !auth.refresh) auth = null;
}
let refreshing = null;
function refreshSession() {
  if (!refreshing) {
    refreshing = cognito("InitiateAuth", { AuthFlow: "REFRESH_TOKEN_AUTH", ClientId: CONFIG.clientId, AuthParameters: { REFRESH_TOKEN: auth.refresh } })
      .then((d) => keepSession(d.AuthenticationResult, auth)).finally(() => { refreshing = null; });
  }
  return refreshing;
}
async function ensureToken() {
  if (auth && auth.exp - Date.now() < 60000) {
    try { await refreshSession(); } catch (e) { signOut("Your session has ended: sign in again."); throw new Error("Your session has ended: sign in again."); }
  }
}
function signOut(message) {
  auth = null; sessionStorage.removeItem("mra-auth");
  state.info = null; state.view = null; state.runs = null; state.error = message ? { message } : null; state.notice = null;
  render();
}
async function signIn(username, password) {
  const d = await cognito("InitiateAuth", { AuthFlow: "USER_PASSWORD_AUTH", ClientId: CONFIG.clientId, AuthParameters: { USERNAME: username, PASSWORD: password } });
  if (d.AuthenticationResult) { keepSession(d.AuthenticationResult); return { done: true }; }
  if (d.ChallengeName === "NEW_PASSWORD_REQUIRED") return { newPassword: true, session: d.Session, username };
  throw new Error("This account needs a sign-in step this page does not support (" + d.ChallengeName + "): ask the administrator.");
}
async function setNewPassword(username, password, session) {
  const d = await cognito("RespondToAuthChallenge", { ChallengeName: "NEW_PASSWORD_REQUIRED", ClientId: CONFIG.clientId, Session: session,
    ChallengeResponses: { USERNAME: username, NEW_PASSWORD: password } });
  if (!d.AuthenticationResult) throw new Error("The password was changed but sign-in did not finish: sign in again.");
  keepSession(d.AuthenticationResult);
}

// ---- files: hosted uploads go straight to S3 (the API cannot carry large files); local ones travel inside the request -------------------------------------
async function uploadFile(file, purpose) {
  const s = await api("POST", "/api/uploads", { purpose, name: file.name, size: file.size });
  if (s.mode === "put") {
    const r = await fetch(s.url, { method: "PUT", headers: authHeaders(), body: file });
    if (!r.ok) throw new Error(((await r.json().catch(() => ({}))).error) || "The upload failed.");
  } else {
    const form = new FormData();
    for (const [k, v] of Object.entries(s.fields)) form.append(k, v);
    form.append("file", file);                                              // S3 requires the file to be the last field
    const r = await fetch(s.url, { method: "POST", body: form });
    if (!r.ok) throw new Error("The upload to storage failed (" + r.status + "): try again. Files over " + Math.round(s.limit / 1000000) + " MB are refused.");
  }
  return s.upload_id;
}
async function fileRef(file, purpose) {
  return CONFIG ? { upload_id: await uploadFile(file, purpose) } : { file_b64: await fileToB64(file) };
}
async function download(entry) {
  const link = await busy("Preparing the download...", () => api("POST", entry.url + "/link", {}));
  if (!link) return;
  const a = el("a", { href: link.needs_token ? safeHref(link.url) : link.url, download: entry.name });
  document.body.append(a); a.click(); a.remove();
}

function fileToB64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
    r.onerror = () => reject(new Error("could not read " + file.name));
    r.readAsDataURL(file);
  });
}

const fmt = {
  score: (g) => (g.status === "complete" && g.score !== null ? g.score.toFixed(1) : "Incomplete"),
  colour: (c) => (c === "incomplete" ? "Incomplete" : c),
  date: (s) => (s ? s.replace("T", " ").replace(/\+00:00$/, " UTC") : ""),
};
const chip = (colour) => el("span", { class: "chip c-" + colour }, fmt.colour(colour));
const tagOf = (source) => (source ? el("span", { class: "tag" }, source) : null);
function bar(g) {
  if (g.status !== "complete") return el("span", { class: "muted" }, "Incomplete: a rating is blank");
  const colour = { green: "#00cc00", amber: "#ffff66", red: "#ff9999" }[g.colour] || "#ccc";
  return el("div", { class: "bar" }, el("span", { style: { width: Math.max(0, Math.min(100, (g.score / 5) * 100)) + "%", background: colour } }));
}
const safeHref = (u) => (typeof u === "string" && u.startsWith("/api/") ? u + (u.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN) : "#");

// ---- feedback ------------------------------------------------------------------------------------------
function showError(err) {
  const d = err && err.data;
  const list = d && (d.errors || d.blockers);
  state.error = { message: err.message || String(err), list: list || null };
  state.notice = null;
}
function banners() {
  const out = [];
  if (state.error) out.push(el("div", { class: "banner err", role: "alert" }, el("strong", {}, state.error.message),
    state.error.list ? el("ul", {}, state.error.list.map((x) => el("li", {}, x))) : null));
  if (state.notice) out.push(el("div", { class: "banner ok", role: "status" }, state.notice));
  return out;
}
function updateBanners() {                          // errors and notices change only this strip, so a form keeps what was typed in it
  const box = $("#banners");
  if (box) box.replaceChildren(...banners());
}
function fail(message) { state.error = { message }; state.notice = null; updateBanners(); }
function setBusyNote(text) { const n = $(".overlay .note"); if (n) n.textContent = text; }
async function busy(message, fn) {
  state.error = null; state.notice = null; updateBanners();
  const overlay = el("div", { class: "overlay" }, el("div", {}, message, el("div", { class: "note muted small" }, "")));
  document.body.append(overlay);
  try { return await fn(); } catch (e) { showError(e); updateBanners(); return undefined; } finally { overlay.remove(); }
}

// ---- routing ----------------------------------------------------------------------------------------------
function route() {
  const p = (location.hash.replace(/^#/, "") || "/").split("/").filter(Boolean);
  if (p[0] === "run" && p[1]) return { page: "run", id: decodeURIComponent(p[1]), tab: p[2] || "overview" };
  if (p[0] === "settings") return { page: "settings" };
  return { page: "runs" };
}
const go = (hash) => { location.hash = hash; };

async function loadRun(id) {
  try { state.view = await api("GET", "/api/runs/" + encodeURIComponent(id)); state.error = null; }
  catch (e) { state.view = null; showError(e); }
}
async function loadRuns() {
  try { state.runs = (await api("GET", "/api/runs")).runs; } catch (e) { showError(e); }
}
async function loadInfo() {
  try { state.info = await api("GET", "/api/info"); } catch (e) { showError(e); }
}
async function reload(notice) {
  const r = route();
  if (r.page === "run") await loadRun(r.id);
  if (notice) state.notice = notice;
  render();
}
async function act(message, fn, notice) {          // run one action, then refresh the run
  const done = await busy(message, async () => { await fn(); await loadRun(route().id); return true; });
  if (done && notice) { state.notice = notice; render(); }
}

// ---- the frame ---------------------------------------------------------------------------------------------
function header() {
  const info = state.info;
  const real = info && info.engine !== "stub";
  return el("header", { class: "top" },
    el("h1", {}, el("a", { href: "#/" }, "A04 MRA Agent")),
    info ? el("span", { class: "badge" + (real ? " real" : ""), title: info.model_id }, real ? "Text engine: " + info.engine + " (real model, uses AWS)" : "Text engine: stub (offline, no AWS)") : null,
    info ? el("span", { class: "small", title: info.storage_where }, "Data: " + (info.storage === "aws" ? "S3, encrypted" : "this computer")) : null,
    el("span", { class: "grow" }),
    CONFIG ? (auth ? el("span", { class: "small", id: "whoami" }, "Signed in as " + auth.name + (info && info.user && !info.user.can_write ? " (read only)" : "")) : null) :
      el("label", {}, "Your name",
        el("input", { type: "text", value: state.actor, placeholder: info ? info.default_actor : "", maxlength: "60", id: "actor",
          onchange: (e) => { state.actor = e.target.value.trim(); localStorage.setItem("mra-actor", state.actor); } })),
    auth ? el("a", { href: "#/settings" }, "Settings") : (CONFIG ? null : el("a", { href: "#/settings" }, "Settings")),
    CONFIG && auth ? el("a", { href: "#", id: "signout", onclick: (e) => { e.preventDefault(); signOut(); } }, "Sign out") : null);
}

function loginPage() {
  const user = el("input", { type: "text", id: "username", autocomplete: "username", autocapitalize: "none", spellcheck: "false" });
  const pass = el("input", { type: "password", id: "password", autocomplete: "current-password" });
  const go1 = async () => {
    if (!user.value.trim() || !pass.value) { fail("Enter your username and password."); return; }
    const out = await busy("Signing in...", () => signIn(user.value.trim(), pass.value));
    if (!out) return;
    if (out.newPassword) { state.challenge = out; render(); return; }
    await afterSignIn();
  };
  const onEnter = (e) => { if (e.key === "Enter") go1(); };
  user.addEventListener("keydown", onEnter); pass.addEventListener("keydown", onEnter);
  if (state.challenge) {
    const a = el("input", { type: "password", id: "new1", autocomplete: "new-password" });
    const b = el("input", { type: "password", id: "new2", autocomplete: "new-password" });
    const save = async () => {
      if (a.value !== b.value) { fail("The two passwords are not the same."); return; }
      if (a.value.length < 10) { fail("Use at least 10 characters, with a capital letter, a small letter and a digit."); return; }
      const done = await busy("Saving the new password...", () => setNewPassword(state.challenge.username, a.value, state.challenge.session));
      if (done !== undefined || auth) { state.challenge = null; await afterSignIn(); }
    };
    return el("div", { class: "card login" }, el("h2", {}, "Choose a new password"),
      el("p", { class: "muted" }, "This is your first sign-in. At least 10 characters, with a capital letter, a small letter and a digit."),
      el("label", { class: "f" }, el("span", {}, "New password"), a), el("label", { class: "f" }, el("span", {}, "Repeat it"), b),
      el("button", { id: "save-password", onclick: save }, "Save and sign in"));
  }
  return el("div", { class: "card login" }, el("h2", {}, "Sign in"), el("p", { class: "muted" }, "A04 MRA Agent. Use the username and password you were given."),
    el("label", { class: "f" }, el("span", {}, "Username"), user), el("label", { class: "f" }, el("span", {}, "Password"), pass),
    el("button", { id: "signin", onclick: go1 }, "Sign in"));
}
async function afterSignIn() {
  state.error = null; state.runs = null; lastRoute = "";
  await loadInfo();
  await onRoute();
}

function render() {
  const app = $("#app");
  const r = route();
  let body;
  if (CONFIG && !auth) body = loginPage();
  else if (!state.info) body = el("p", { class: "loading" }, "Loading...");
  else if (r.page === "settings") body = settingsPage();
  else if (r.page === "run") body = state.view && state.view.set_id === r.id ? runPage(r) : el("p", {}, state.error ? "" : "Loading the run...");
  else body = runsPage();
  const keep = document.activeElement && document.activeElement.id === "actor";
  const y = window.scrollY;
  app.replaceChildren(header(), el("main", {}, el("div", { id: "banners" }, ...banners()), body));
  window.scrollTo(0, y);
  if (keep) $("#actor").focus();
}

// ---- runs page -------------------------------------------------------------------------------------------------
function runsPage() {
  const f = {};
  const field = (key, label, attrs) => el("label", { class: "f" }, el("span", {}, label), (f[key] = el("input", { type: "text", ...attrs })));
  const submit = async () => {
    const q = f.file.files[0];
    if (!q) { fail("Choose the filled-in questionnaire file first."); return; }
    const meta = {};
    for (const k of state.info.meta_fields) if (f[k] && f[k].value.trim()) meta[k] = f[k].value.trim();
    const done = await busy("Reading and scoring the questionnaire... (with a real model this can take a minute)", async () => {
      const notes = [];
      for (const n of f.notes.files) notes.push(CONFIG ? { name: n.name, upload_id: await uploadFile(n, "note") } : { name: n.name, content: await n.text() });
      const out = await api("POST", "/api/runs", { file_name: q.name, ...(await fileRef(q, "questionnaire")), set_id: f.set_id.value.trim(),
        customer: f.customer.value.trim(), notes, meta });
      return out.set_id;
    });
    if (done) { state.keepNotice = true; state.notice = "Scored. Check the blank ratings and the proposed actions, then work through the Release tab."; go("#/run/" + encodeURIComponent(done) + "/overview"); }
  };
  const form = el("div", { class: "card" }, el("h2", {}, "New assessment"),
    el("p", { class: "muted" }, "Upload the filled-in AWS questionnaire (the Questions sheet) and the interview notes. Uploading again with the same run id replaces its answers and notes (a finalised run must be reopened first)."),
    el("div", { class: "grid" },
      el("label", { class: "f" }, el("span", {}, "Filled-in questionnaire (.xlsx or .csv) *"), (f.file = el("input", { type: "file", accept: ".xlsx,.xlsm,.csv,.tsv" }))),
      el("label", { class: "f" }, el("span", {}, "Interview notes (.txt or .md, several allowed)"), (f.notes = el("input", { type: "file", accept: ".txt,.md", multiple: true }))),
      field("customer", "Customer name", { placeholder: "from the file name if empty" }),
      field("set_id", "Run id", { placeholder: "from the file name if empty" }),
      field("facilitator", "Facilitator (needed to finalise)", {}), field("presenter", "Presenter", {}),
      el("label", { class: "f" }, el("span", {}, "Workshop date (needed to finalise)"), (f.workshop_date = el("input", { type: "date" }))),
      el("label", { class: "f" }, el("span", {}, "Readout date (needed to finalise)"), (f.readout_date = el("input", { type: "date" }))),
      field("internal_contact", "Internal contact", {}), field("sponsor_contact", "Sponsor contact", {})),
    el("div", { class: "row" }, el("button", { onclick: submit }, "Score it")));
  const runs = state.runs;
  const table = !runs ? el("p", { class: "muted" }, "Loading runs...") : runs.length === 0 ? el("p", { class: "muted" }, "No runs yet.") :
    el("table", {}, el("tr", {}, ["Customer", "Run id", "State", "Blank ratings", "Text engine", "Last run", ""].map((h) => el("th", {}, h))),
      runs.map((r) => el("tr", {}, el("td", {}, r.customer), el("td", {}, r.set_id),
        el("td", {}, r.final ? "FINAL" : "DRAFT"), el("td", {}, r.blank_ratings || "none"), el("td", {}, r.engine || ""),
        el("td", {}, fmt.date(r.generated_at)), el("td", {}, el("a", { class: "btn secondary", href: "#/run/" + encodeURIComponent(r.set_id) + "/overview" }, "Open")))));
  return el("div", {}, form, el("div", { class: "card" }, el("h2", {}, "Runs"), table));
}

// ---- one run -------------------------------------------------------------------------------------------------------------
const TABS = [["overview", "Overview"], ["ratings", "Ratings"], ["activities", "Activities"], ["validation", "Validation"], ["proposals", "Proposals"],
  ["answers", "Answers and notes"], ["release", "Release"], ["outputs", "Outputs"]];

function counts(v) {
  return { ratings: v.blank_ratings.length, activities: v.activities.filter((a) => a.decision === "pending").length,
    validation: v.validation.filter((i) => i.resolution === "open" && i.kind !== "blank_rating").length,
    proposals: v.proposals.filter((p) => p.status === "proposed").length, release: v.status.blockers.length };
}

function runPage(r) {
  const v = state.view, c = counts(v);
  const tabs = el("nav", { class: "tabs" }, TABS.map(([key, label]) => el("a", { class: key === r.tab ? "on" : "", href: "#/run/" + encodeURIComponent(v.set_id) + "/" + key },
    label, c[key] ? el("span", { class: "count" + (key === "ratings" ? " warn" : "") }, c[key]) : null)));
  const pages = { overview, ratings, activities, validation, proposals, answers, release, outputs };
  return el("div", {},
    state.info.user && !state.info.user.can_write ? el("div", { class: "banner info", id: "readonly" }, "This account can read but not change anything.") : null,
    el("div", { class: "row" }, el("h2", {}, v.customer), el("span", { class: "chip " + (v.status.final ? "c-green" : "c-amber") }, v.report_status),
      el("span", { class: "muted small" }, "run " + v.set_id + " | " + fmt.date(v.generated_at) + " | text: " + (v.engine || "")),
      el("span", { class: "grow" }), el("button", { class: "secondary", onclick: () => reload("Refreshed.") }, "Refresh")),
    tabs, (pages[r.tab] || overview)(v));
}

function groupTable(title, rows) {
  return el("div", { class: "card" }, el("h3", { class: "first" }, title),
    el("table", {}, el("tr", {}, ["", "Score (1 to 5)", "", "Rated"].map((h) => el("th", {}, h))),
      rows.map((g) => el("tr", {}, el("td", {}, g.name), el("td", { class: "num" }, g.status === "complete" ? [fmt.score(g), " "] : null, chip(g.colour)), el("td", {}, bar(g)),
        el("td", { class: "num" }, g.answered + " of " + g.total)))));
}

function overview(v) {
  const t = v.traceability, f = v.focus;
  return el("div", {},
    v.blank_ratings.length ? el("div", { class: "banner warn" }, el("strong", {}, v.blank_ratings.length + " rating(s) are blank: " + v.blank_ratings.join(", ") + ". "),
      "The groups that contain them show as Incomplete (never 0) and release is blocked. ", el("a", { href: "#/run/" + encodeURIComponent(v.set_id) + "/ratings" }, "Give them a rating")) : null,
    v.input_warnings.length ? el("div", { class: "banner info" }, el("strong", {}, "Notes from reading the questionnaire"), el("ul", {}, v.input_warnings.map((w) => el("li", {}, w)))) : null,
    el("div", { class: "card" }, el("h3", { class: "first" }, "Summary", tagOf(v.narrative.source)), el("p", {}, v.narrative.text)),
    el("div", { class: "grid" }, groupTable("Perspectives", v.perspectives), groupTable("Workstreams", v.workstreams)),
    el("div", { class: "card" }, el("h3", { class: "first" }, "What the deck will say", el("span", { class: "tag" }, "rule-based")),
      el("p", { class: "muted small" }, "These sentences and lists are produced by fixed rules from the scores (the same wording every run). Incomplete groups are left out."),
      el("ul", { class: "plain" }, [...f.perspective_sentences, ...f.workstream_sentences].map((s) => el("li", {}, s))),
      el("div", { class: "grid" }, el("div", {}, el("strong", {}, "Strengths (Pre-Mobilize)"), el("ul", { class: "plain" }, f.strengths.map((s) => el("li", {}, s)))),
        el("div", {}, el("strong", {}, "Gaps (Pre-Mobilize)"), el("ul", { class: "plain" }, f.gaps.map((s) => el("li", {}, s)))),
        ...f.key_observations.map((p, i) => el("div", {}, el("strong", {}, "Key observations, page " + (i + 1)),
          el("ul", { class: "plain" }, [...p.strong.map((s) => el("li", {}, s + " (strong)")), ...p.weak.map((s) => el("li", {}, s + " (weak)"))]))))),
    el("div", { class: "card" }, el("h3", { class: "first" }, "Evidence"),
      el("p", {}, t.evidenced + " of " + t.scored_questions + " rated questions have supporting evidence" + (t.evidenced_pct !== null ? " (" + t.evidenced_pct + "%)." : "."),
        " Every other rated question is listed on the Validation tab."),
      v.notes.length === 0 ? el("p", { class: "muted" }, "No interview notes were uploaded, so nothing can be evidenced yet.") : null),
    v.placeholder_notes.length ? el("details", {}, el("summary", {}, "Settings that are inferred or proposed, not confirmed by Ingram"),
      el("ul", { class: "plain" }, v.placeholder_notes.map((n) => el("li", {}, n)))) : null);
}

// ---- ratings -----------------------------------------------------------------------------------------------------------------
function ratingForm(v, q, label) {
  const name = "lv-" + q.question_id;
  let chosen = q.score;
  const reason = el("textarea", { placeholder: "Why (required; kept in the audit log)" });
  const radios = q.levels.map((txt, i) => {
    const lv = i + 1;
    const input = el("input", { type: "radio", name, value: lv, checked: q.score === lv,
      onchange: () => { chosen = lv; for (const l of lbls) l.classList.toggle("sel", l.dataset.lv === String(lv)); } });
    const lab = el("label", { "data-lv": lv, class: q.score === lv ? "sel" : "" }, input, txt);
    return lab;
  });
  const lbls = radios;
  const save = el("button", { onclick: () => {
    if (!chosen) { fail("Choose a level first."); return; }
    act("Saving the rating and recomputing...", () => api("POST", "/api/runs/" + encodeURIComponent(v.set_id) + "/adjust", { question: q.question_id, score: chosen, reason: reason.value }),
      q.question_id + " set to " + chosen + ". Scores must be confirmed again on the Release tab.");
  } }, label);
  return el("div", {}, el("div", { class: "levels" }, radios), el("label", { class: "f" }, el("span", {}, "Reason"), reason), save);
}

function ratings(v) {
  const acts = Object.fromEntries(v.activities.map((a) => [a.id, a.name]));
  const scored = v.questions.filter((q) => q.perspective);
  const blank = scored.filter((q) => q.score === null);
  const rest = scored.filter((q) => q.score !== null);
  return el("div", {},
    el("p", { class: "muted" }, "One agreed rating per question. A blank rating makes its groups Incomplete and blocks release. A rating you set here is recorded with your name and reason, and the original is kept."),
    blank.length ? el("div", {}, el("h3", {}, "Blank ratings (" + blank.length + ")"), blank.map((q) => el("div", { class: "card" },
      el("strong", {}, q.question_id + " | " + q.category + " | " + (acts[q.activity] || "")), el("p", {}, q.text),
      el("p", { class: "muted small" }, "Read the answers and notes first (Answers and notes tab). Pick the level that matches; if nothing matches, leave it blank."),
      ratingForm(v, q, "Set this rating")))) : el("div", { class: "banner ok" }, "Every scored question has a rating."),
    el("h3", {}, "All ratings (" + rest.length + ")"),
    el("table", {}, el("tr", {}, ["Q", "Activity", "Question", "Rating", "What that level says", "Evidence", ""].map((h) => el("th", {}, h))),
      rest.map((q) => el("tr", {}, el("td", {}, q.question_id), el("td", {}, acts[q.activity] || ""), el("td", { class: "clamp" }, q.text),
        el("td", { class: "num" }, q.score, q.adjusted ? el("span", { class: "tag", title: q.adjustment ? q.adjustment.reason : "" }, "adjusted from " + q.original_score) : null),
        el("td", {}, q.level_text), el("td", {}, q.traceable ? "yes" : "none"),
        el("td", {}, el("details", {}, el("summary", {}, "Change"), ratingForm(v, q, "Change this rating")))))));
}

// ---- activities --------------------------------------------------------------------------------------------------------------------
function decisionForm(v, a) {
  const first = a.actions[0] || {};
  const cur = ["approve", "edit", "reject"].includes(a.decision) ? a.decision : "approve";
  const sel = el("select", {}, ["approve", "edit", "reject"].map((d) => el("option", { value: d, selected: d === cur }, { approve: "Approve", edit: "Approve with my own wording", reject: "Reject" }[d])));
  const text = el("textarea", { placeholder: "Your wording (only used with 'my own wording')" }, "");
  const owner = el("input", { type: "text", value: first.owner || "", placeholder: "Name (role)" });
  const due = el("input", { type: "date", value: first.due_date || "" });
  const comment = el("input", { type: "text", value: a.comment || "", placeholder: "Comment (required to reject)" });
  return el("div", {}, el("div", { class: "grid" },
    el("label", { class: "f" }, el("span", {}, "Decision"), sel), el("label", { class: "f" }, el("span", {}, "Owner"), owner),
    el("label", { class: "f" }, el("span", {}, "Due date"), due), el("label", { class: "f" }, el("span", {}, "Comment"), comment)),
    el("label", { class: "f" }, el("span", {}, "Wording"), text),
    el("button", { onclick: () => act("Saving the decision...", () => api("POST", "/api/runs/" + encodeURIComponent(v.set_id) + "/decide",
      { activity: a.id, decision: sel.value, text: text.value, owner: owner.value, due_date: due.value, comment: comment.value }), "Decision saved for " + a.name + ".") }, "Save decision"));
}

function activityCard(v, a) {
  const head = el("div", { class: "row" }, el("strong", {}, a.id + " " + a.name), chip(a.colour), el("span", {}, fmt.score(a)), el("span", { class: "tag" }, a.phase),
    el("span", { class: "tag" }, a.needs_action ? "decision: " + a.decision : "no action needed"));
  const obs = el("p", {}, el("em", {}, "Observation"), tagOf(a.observation.source), ": ", a.observation.text);
  let actions;
  if (a.needs_action) {
    const list = el("ul", { class: "plain" }, a.actions.map((x) => el("li", {}, x.text, tagOf(x.source),
      el("span", { class: "muted small" }, " | owner: " + (x.owner || "to be assigned") + " | due: " + (x.due_date || "to be set") + " | " + x.status))));
    actions = el("div", {}, el("em", {}, "Proposed action(s)"), list,
      el("details", { open: a.decision === "pending" }, el("summary", {}, "Decide"), decisionForm(v, a)));
  } else {
    actions = el("p", { class: "muted" }, "No action required.");
  }
  return el("div", { class: "card" }, head, obs, actions);
}

function activities(v) {
  const groups = {};
  for (const a of v.activities) (groups[a.workstream_name] = groups[a.workstream_name] || []).push(a);
  return el("div", {},
    el("p", { class: "muted" }, "The observation is the questionnaire's own wording unless a model rewrote it (labelled). Actions come from the action library or the next rating level. Nothing is final until you decide."),
    Object.entries(groups).map(([ws, list]) => el("div", {}, el("h3", {}, ws), list.map((a) => activityCard(v, a))))); 
}

// ---- validation -----------------------------------------------------------------------------------------------------------------------
function validation(v) {
  let filter = "open";
  const holder = el("div", {});
  const draw = () => {
    const rows = v.validation.filter((i) => (filter === "all" ? true : filter === "open" ? i.resolution === "open" : i.kind === filter));
    holder.replaceChildren(...rows.map((i) => {
      const res = el("select", {}, ["resolved", "accepted_risk", "not_applicable"].map((r) => el("option", { value: r, selected: r === (i.resolution === "open" ? "accepted_risk" : i.resolution) }, r.replace("_", " "))));
      const note = el("input", { type: "text", value: i.note || "", placeholder: "Note (required)" });
      return el("div", { class: "card" }, el("div", { class: "row" }, el("strong", {}, i.item_id), el("span", { class: "tag" }, i.kind.replace(/_/g, " ")),
        el("span", {}, i.reason), el("span", { class: "grow" }), el("span", { class: "chip " + (i.resolution === "open" ? "c-amber" : "c-green") }, i.resolution.replace("_", " "))),
        i.followup ? el("div", { class: "quote" }, "Follow-up drafted for " + i.followup.audience_roles.join(", ") + ":\n" + i.followup.questions.map((q) => "- " + q).join("\n")) : null,
        i.kind === "blank_rating" ? el("p", { class: "muted" }, "A blank rating is not resolved with a note: give it a rating on the Ratings tab.") :
          el("div", { class: "row" }, res, note, el("button", { class: "secondary", onclick: () => act("Saving...", () => api("POST", "/api/runs/" + encodeURIComponent(v.set_id) + "/resolve",
            { item: i.key, resolution: res.value, note: note.value }), i.key + " resolved.") }, "Save")));
    }), rows.length ? null : el("p", { class: "muted" }, "Nothing here."));
  };
  const kinds = ["open", "all", ...new Set(v.validation.map((i) => i.kind))];
  const filters = el("div", { class: "row" }, kinds.map((k) => el("button", { class: "secondary", onclick: () => { filter = k; draw(); } }, k.replace(/_/g, " "))));
  draw();
  return el("div", {}, el("p", { class: "muted" }, "Every rated question is either evidenced or listed here. Resolving an item needs a note; it is recorded with your name."), filters, holder);
}

// ---- proposals -------------------------------------------------------------------------------------------------------------------------
const TASKS = [["rating", "Rating: propose a rating for each blank question"], ["evidence", "Evidence: find note passages that support ratings"],
  ["investigate", "Investigate: draft follow-up questions"], ["context", "Context: extract priorities and constraints for planning"]];

function excerpt(ex, fallbackQuote) {
  if (!ex) return null;
  return el("div", { class: "quote" }, ex.found ? [ex.before, el("mark", {}, ex.quote), ex.after] : [fallbackQuote, el("span", { class: "muted small" }, "\n(the passage could not be located in the stored note)")]);
}

function proposalCard(v, p) {
  const d = p.payload;
  let body;
  if (p.kind === "rating") body = [el("p", {}, el("strong", {}, d.question_id + " = " + d.score), " | level text: " + d.level_text), el("p", {}, "Why: " + d.rationale), excerpt(p.excerpt, d.quote)];
  else if (p.kind === "evidence_link") body = [el("p", {}, el("strong", {}, "Note " + d.evidence_id + " supports " + d.question_id)), excerpt(p.excerpt, d.quote)];
  else if (p.kind === "context_item") body = [el("p", {}, el("strong", {}, d.kind + ": "), d.text), excerpt(p.excerpt, d.quote)];
  else body = [el("p", {}, el("strong", {}, "Follow-up for " + d.item_key), " to " + d.audience_roles.join(", ")), el("ul", { class: "plain" }, d.questions.map((q) => el("li", {}, q))), el("p", { class: "muted" }, d.rationale)];
  const comment = el("input", { type: "text", placeholder: "Comment (optional)" });
  const decide = (which) => act("Saving...", () => api("POST", "/api/runs/" + encodeURIComponent(v.set_id) + "/proposals", { [which]: [p.id], comment: comment.value }),
    p.id + (which === "accept" ? " accepted." : " rejected."));
  return el("div", { class: "card" }, el("div", { class: "row" }, el("strong", {}, p.id), el("span", { class: "tag" }, p.kind.replace(/_/g, " ")), el("span", { class: "tag" }, "task: " + p.task),
    el("span", { class: "chip " + ({ proposed: "c-amber", accepted: "c-green", rejected: "c-incomplete" }[p.status]) }, p.status)),
    body, p.decision ? el("p", { class: "muted small" }, p.status + " by " + p.decision.by + " at " + fmt.date(p.decision.at) + (p.decision.comment ? ": " + p.decision.comment : "")) : null,
    el("div", { class: "row" }, comment,
      p.status !== "accepted" ? el("button", { onclick: () => decide("accept") }, "Accept") : null,
      p.status !== "rejected" ? el("button", { class: "secondary", onclick: () => decide("reject") }, p.status === "accepted" ? "Revoke (reject)" : "Reject") : null));
}

function proposals(v) {
  const sel = el("select", {}, TASKS.map(([k, label]) => el("option", { value: k }, label)));
  const out = el("div", {});
  const run = async () => {
    const res = await busy("The agent is working... (with a real model this can take a minute)", async () => {
      const r = await api("POST", "/api/runs/" + encodeURIComponent(v.set_id) + "/agent", { task: sel.value });
      await loadRun(v.set_id);
      return r;
    });
    if (res) { state.notice = "Agent task '" + sel.value + "' stopped because " + res.stop_reason + " after " + res.tool_calls + " tool calls; " + res.proposal_ids.length + " proposal(s)." + (res.summary ? " " + res.summary : ""); render(); }
  };
  const groups = [["Waiting for your decision", "proposed"], ["Accepted", "accepted"], ["Rejected", "rejected"]];
  return el("div", {}, el("div", { class: "card" }, el("h3", { class: "first" }, "Run the agent"),
    el("p", { class: "muted" }, "The agent can only read and propose. Nothing changes until you accept. A rating proposal needs an exact quote from a note; accepting it records a rating adjustment in your name." +
      (state.info.engine === "stub" ? " The offline stub is a fixed script, not a model: it finds simple matches and never proposes a rating." : "")),
    el("div", { class: "row" }, sel, el("button", { onclick: run }, "Run"))), out,
    groups.map(([title, st]) => { const list = v.proposals.filter((p) => p.status === st); return list.length ? el("div", {}, el("h3", {}, title + " (" + list.length + ")"), list.map((p) => proposalCard(v, p))) : null; }),
    v.proposals.length ? null : el("p", { class: "muted" }, "No proposals yet."));
}

// ---- answers and notes --------------------------------------------------------------------------------------------------------------------
function answers(v) {
  return el("div", {}, el("h3", {}, "Free-text and Yes/No answers"),
    el("table", {}, el("tr", {}, ["#", "Category", "Question", "Answer"].map((h) => el("th", {}, h))),
      v.text_answers.map((t) => el("tr", {}, el("td", {}, t.number), el("td", {}, t.category), el("td", { class: "clamp" }, t.question), el("td", {}, t.answer || el("span", { class: "muted" }, "(blank)"))))),
    el("h3", {}, "Interview notes (" + v.notes.length + ")"), v.notes.length ? null : el("p", { class: "muted" }, "None uploaded."),
    v.notes.map((n) => el("details", {}, el("summary", {}, n.id + " | " + n.source + (n.applies_to.length ? " | applies to " + n.applies_to.join(", ") : "")), el("pre", {}, n.text))));
}

// ---- release -----------------------------------------------------------------------------------------------------------------------------------
function release(v) {
  const s = v.status, id = encodeURIComponent(v.set_id);
  const meta = {};
  const f = (k, label, type) => el("label", { class: "f" }, el("span", {}, label), (meta[k] = el("input", { type: type || "text", value: v.details[k] || "" })));
  const reason = el("input", { type: "text", placeholder: "Why it is being reopened (required)" });
  const del = el("input", { type: "text", placeholder: "type the run id to delete it" });
  return el("div", {},
    s.final ? el("div", { class: "banner ok" }, el("strong", {}, "Finalised by " + s.final.by + " at " + fmt.date(s.final.at) + ". "), "The report is locked.") :
      s.ready_to_finalise ? el("div", { class: "banner ok" }, el("strong", {}, "Nothing is outstanding."), " You can finalise.") :
        el("div", { class: "banner warn" }, el("strong", {}, s.blockers.length + " thing(s) stand between this report and release:"), el("ul", {}, s.blockers.map((b) => el("li", {}, b)))),
    s.scores_confirmation_stale ? el("div", { class: "banner err" }, "The scores changed after they were confirmed. Confirm them again.") : null,
    el("div", { class: "card" }, el("h3", { class: "first" }, "Report details"), el("p", { class: "muted small" }, "Printed on the covers. Anything missing shows as TBD. Facilitator and both dates are needed to finalise."),
      el("div", { class: "grid" }, f("facilitator", "Facilitator"), f("presenter", "Presenter"), f("workshop_date", "Workshop date", "date"), f("readout_date", "Readout date", "date"),
        f("internal_contact", "Internal contact"), f("sponsor_contact", "Sponsor contact")),
      el("button", { onclick: () => act("Saving...", () => api("POST", "/api/runs/" + id + "/meta", Object.fromEntries(Object.entries(meta).map(([k, e]) => [k, e.value]))), "Report details saved."), disabled: s.final }, "Save details")),
    el("div", { class: "card" }, el("h3", { class: "first" }, "Confirm and finalise"),
      el("p", {}, "Scores confirmed: ", el("strong", {}, s.scores_confirmed ? "yes" : "no"), ". Blank ratings: ", el("strong", {}, s.blank_ratings), ". Actions decided: ", el("strong", {}, s.actions_decided + " of " + s.actions_total),
        ". Validation items resolved: ", el("strong", {}, s.items_resolved + " of " + s.items_total), "."),
      el("div", { class: "row" },
        el("button", { disabled: s.final || s.blank_ratings > 0, onclick: () => act("Confirming...", () => api("POST", "/api/runs/" + id + "/confirm", {}), "Scores confirmed.") }, "Confirm the scores"),
        el("button", { disabled: s.final, onclick: () => act("Finalising...", () => api("POST", "/api/runs/" + id + "/finalise", {}), "Finalised.") }, "Finalise the report")),
      s.final ? el("div", { class: "row" }, reason, el("button", { class: "secondary", onclick: () => act("Reopening...", () => api("POST", "/api/runs/" + id + "/reopen", { reason: reason.value }), "Reopened. Confirm the scores again before finalising.") }, "Reopen")) : null),
    el("details", {}, el("summary", {}, "Delete this run"), el("p", { class: "muted" }, "Deletes the run, its audit log and its cached model text from this computer. A record that it was deleted is kept."),
      el("div", { class: "row" }, del, el("button", { class: "danger", onclick: async () => {
        const ok = await busy("Deleting...", () => api("POST", "/api/runs/" + id + "/purge", { confirm: del.value }));
        if (ok) { state.view = null; state.runs = null; state.keepNotice = true; state.notice = "Deleted."; go("#/"); }
      } }, "Delete"))));
}

// ---- outputs ---------------------------------------------------------------------------------------------------------------------------------------
const fmtName = (c) => (c && c.format === "ingram" ? "Ingram MAP deliverable" : "AWS MRA readout format");
function coverageNote(entry) {
  if (!entry || entry.builtin) return el("span", { class: "muted small" }, entry ? entry.description : "");
  const c = entry.coverage;
  return c.complete ? el("span", { class: "muted small" }, "Same format as the " + fmtName(c) + ": every part of it can be filled (" + c.found + " of " + c.expected + " targets).")
    : el("span", { class: "small" }, el("strong", {}, "Only part of this deck can be filled: "), c.warnings.join("; "), ". The open-items file lists it too.");
}

function outputs(v) {
  const id = encodeURIComponent(v.set_id), info = state.info;
  const lib = info.templates.pptx;
  const boxes = {};
  const opt = (k, label, hint) => el("label", { class: "row" }, (boxes[k] = el("input", { type: "checkbox", checked: k !== "context", class: "auto" })), label, hint ? el("span", { class: "muted small" }, hint) : null);
  const chosen = () => (state.tplChoice && lib.templates.some((t) => t.id === state.tplChoice) ? state.tplChoice : lib.preferred);
  const note = el("div", {}, coverageNote(lib.templates.find((t) => t.id === chosen())));
  const pick = el("select", { id: "deck-format", onchange: (e) => { state.tplChoice = e.target.value; note.replaceChildren(coverageNote(lib.templates.find((t) => t.id === e.target.value))); } },
    lib.templates.map((t) => el("option", { value: t.id, selected: t.id === chosen() }, t.name + (t.id === lib.preferred && !t.builtin ? " (my default)" : ""))));
  const preview = async (kind) => {
    const r = await busy("Making the preview with LibreOffice... (a deck can take a minute)", async () => {
      const x = await api("POST", "/api/runs/" + id + "/preview", { kind, template: pick.value }); await loadRun(v.set_id); return x;
    });
    if (r) { state.preview = r.url; render(); }
  };
  const k = v.kpis;
  return el("div", {},
    v.status.final ? null : el("div", { class: "banner info" }, "This report is a DRAFT: the files are marked DRAFT and incomplete groups show as Incomplete."),
    el("div", { class: "card" }, el("h3", { class: "first" }, "Create the files"),
      el("div", {}, opt("xlsx", "Excel workbook", " Questions, Actions and Scores in the AWS layout, plus traceability and validation"), opt("docx", "Word report", " the AWS readout structure"),
        opt("pptx", "PowerPoint deck", " filled into the format chosen below (also writes the open-items list)"),
        opt("context", "Planning note (Markdown)", " accepted priorities and constraints for AWS Transform planning")),
      el("label", { class: "f" }, el("span", {}, "Deck format"), pick), note,
      el("p", { class: "muted small" }, "To use your own AWS-format deck, upload it under Settings. It is filled the same way; anything in it the agent cannot find is listed instead of being guessed."),
      el("button", { onclick: async () => {
        const done = await busy("Creating the files...", async () => {
          const r = await api("POST", "/api/runs/" + id + "/export", { kinds: Object.keys(boxes).filter((x) => boxes[x].checked), template: pick.value });
          await loadRun(v.set_id); return r;
        });
        if (done) { state.notice = "Files created." + (Object.keys(boxes).length && boxes.pptx.checked ? " Deck format used: " + done.deck_template + "." : ""); render(); }
      } }, "Create files")),
    el("div", { class: "card" }, el("h3", { class: "first" }, "Files for this run"),
      v.exports.length ? el("ul", { class: "plain" }, v.exports.map((e) => el("li", {}, el("a", { href: "#", class: "download", onclick: (ev) => { ev.preventDefault(); download(e); } }, e.name), el("span", { class: "muted small" }, " " + Math.round(e.size / 1024) + " KB")))) : el("p", { class: "muted" }, "None yet."),
      info.libreoffice ? el("div", { class: "row" }, el("button", { class: "secondary", onclick: () => preview("pptx") }, "Preview the deck"),
        el("button", { class: "secondary", onclick: () => preview("docx") }, "Preview the report")) :
        el("p", { class: "muted" }, info.hosted ? "The preview is not available in the hosted version: download the file and open it in PowerPoint or Word." :
          "Preview needs LibreOffice (brew install --cask libreoffice). Without it, download the files and open them in PowerPoint or Word."),
      state.preview ? el("p", { class: "small" }, el("a", { href: safeHref(state.preview), target: "_blank", rel: "noopener" }, "Open the preview in its own tab")) : null,
      state.preview ? el("iframe", { class: "preview", src: safeHref(state.preview), title: "preview" }) : null),
    el("div", { class: "card" }, el("h3", { class: "first" }, "Measures (SOW KPIs)"), el("ul", { class: "plain" },
      el("li", {}, "Turnaround from first run to finalise: " + (k.turnaround.hours !== null ? k.turnaround.hours + " hours" : "not finalised yet") + " (" + k.turnaround.note + ")"),
      el("li", {}, "Evidence: " + k.evidence_traceability.at_first_run_pct + "% at first run, " + k.evidence_traceability.now_pct + "% now"),
      el("li", {}, "Validation items: " + k.validation_items.at_first_run + " at first run, " + k.validation_items.now + " now, " + k.validation_items.resolved + " resolved"),
      el("li", {}, "Agent proposals accepted: " + (k.agent_proposals.acceptance_rate_pct === null ? "none decided yet" : k.agent_proposals.acceptance_rate_pct + "%")),
      el("li", {}, "Audit log: " + v.audit.entries + " entries, " + (v.audit.ok ? "chain intact" : "PROBLEM: " + v.audit.problem)))));
}

// ---- settings --------------------------------------------------------------------------------------------------------------------------------------------
function coverageTable(c) {
  return el("table", {}, el("tr", {}, ["Part of the deck", "Found", "In the default"].map((h) => el("th", {}, h))),
    c.items.map((i) => el("tr", {}, el("td", {}, i.label), el("td", { class: "num" }, (i.ok ? "" : "MISSING: ") + i.found), el("td", { class: "num" }, i.expected))));
}

function deckLibrary() {
  const lib = state.info.templates.pptx;
  const input = el("input", { type: "file", accept: ".pptx" });
  const prefer = el("input", { type: "checkbox", class: "auto", checked: true });
  const refresh = async (notice) => { await loadInfo(); if (notice) state.notice = notice; render(); };
  const rows = lib.templates.map((t) => el("div", { class: "card" },
    el("div", { class: "row" }, el("strong", {}, t.name), t.builtin ? el("span", { class: "tag" }, "always available") : null,
      el("span", { class: "chip " + (t.id === lib.preferred ? "c-green" : "c-incomplete") }, t.id === lib.preferred ? "used by default" : "not the default"),
      el("span", { class: "grow" }),
      t.id !== lib.preferred ? el("button", { class: "secondary", onclick: async () => { if (await busy("Saving...", () => api("POST", "/api/templates/" + t.id + "/prefer", {}))) refresh("Now using: " + t.name); } }, "Use this by default") : null,
      t.builtin ? null : el("button", { class: "danger", onclick: async () => { if (await busy("Removing...", () => api("POST", "/api/templates/" + t.id + "/delete", {}))) refresh("Removed " + t.name + "."); } }, "Remove")),
    coverageNote(t), t.builtin ? null : el("details", {}, el("summary", {}, "What can be filled in this deck"), coverageTable(t.coverage))));
  return el("div", {}, el("h3", {}, "Deck format (PowerPoint)"),
    el("p", { class: "muted" }, "Two formats are built in: the Ingram MAP deliverable (the default) and the AWS MRA readout. Upload your own deck in either format to get the outputs in that format instead. The agent fills what it can recognise (charts, heatmap, sentences, tables) and lists anything it cannot find."),
    rows,
    el("div", { class: "card" }, el("h3", { class: "first" }, "Add a deck"), input, el("p", {}),
      el("label", { class: "row" }, prefer, "Use it by default from now on"),
      el("button", { onclick: async () => {
        const f = input.files[0];
        if (!f) { fail("Choose a .pptx file first."); return; }
        const r = await busy("Checking the deck against the default format...", async () => api("POST", "/api/templates", { kind: "pptx", file_name: f.name, ...(await fileRef(f, "template")), prefer: prefer.checked }));
        if (r) refresh("Added " + r.name + ". " + (r.warnings.length ? "Only part of it can be filled: " + r.warnings.join("; ") + "." : "It has the same format as the " + fmtName(r.coverage) + ": every part can be filled."));
      } }, "Add")));
}

function settingsPage() {
  const info = state.info;
  const docInput = el("input", { type: "file", accept: ".docx" });
  return el("div", {}, el("h2", {}, "Settings"), deckLibrary(),
    el("div", { class: "card" }, el("h3", { class: "first" }, "Word report template (optional)"),
      el("p", {}, info.templates.docx ? "Stored: " + info.templates.docx : "None: the report is built in the AWS readout structure."), docInput, el("p", {}),
      el("button", { onclick: async () => {
        const f = docInput.files[0];
        if (!f) { fail("Choose a .docx file first."); return; }
        const r = await busy("Checking...", async () => api("POST", "/api/templates", { kind: "docx", file_name: f.name, ...(await fileRef(f, "template")) }));
        if (r) { await loadInfo(); state.notice = "Stored " + r.name + "."; render(); }
      } }, "Upload")),
    el("div", { class: "card" }, el("h3", { class: "first" }, "About this installation"), el("ul", { class: "plain" },
      el("li", {}, "Version " + info.version), el("li", {}, "Text engine: " + (info.engine === "stub" ? "stub (offline, no AWS)" : info.engine + " (" + info.model_id + ")")),
      el("li", {}, "Data stored in: " + info.storage_where), el("li", {}, "Configuration fingerprint: " + info.config_hash),
      el("li", {}, "LibreOffice for previews: " + (info.libreoffice ? "found" : "not found")),
      ...info.placeholder_notes.map((n) => el("li", { class: "muted" }, "Inferred or proposed: " + n)))));
}

// ---- start -------------------------------------------------------------------------------------------------------------------------------------------------
let lastRoute = "";
async function onRoute() {
  if (state.keepNotice) state.keepNotice = false; else { state.notice = null; state.error = null; }
  const r = route();
  const key = r.page + ":" + (r.id || "");
  if (r.page === "run" && (key !== lastRoute || !state.view)) { state.preview = null; await loadRun(r.id); }
  if (r.page === "runs" && key !== lastRoute) { await loadRuns(); }
  if (r.page === "settings") await loadInfo();
  lastRoute = key;
  render();
}
window.addEventListener("hashchange", onRoute);
(async () => {
  try { const r = await fetch("/config.json", { cache: "no-store" }); const c = r.ok ? await r.json() : null; if (c && c.hosted) CONFIG = c; } catch (e) { CONFIG = null; }
  if (CONFIG) { restoreSession(); if (!auth) { render(); return; } }
  await loadInfo();
  await onRoute();
})();
