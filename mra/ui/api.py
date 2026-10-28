"""Everything the local review page can do, as plain functions over AssessmentService. No HTTP in here, so it is testable alone.

The page never calculates anything: every number, colour and sentence it shows comes from the service. One lock serialises all
calls, so the page cannot trigger the last-write-wins problem of two simultaneous reviewers.
"""
from __future__ import annotations

import base64
import binascii
import getpass
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .. import __version__
from ..agent.tasks import TASKS
from ..inputs.questionnaire import load_input
from ..loader import META_FIELDS, SET_ID, InputValidationError, build_response_set, sha256
from ..outputs.deck_template import DEFAULT_LABEL, coverage, default_template_bytes
from ..outputs.ingram_deck import INGRAM_LABEL, ingram_template_bytes
from ..report_model import build_report_model
from ..review import workflow as wf
from ..service import AssessmentService, rs_to_raw

MAX_NOTES = 200
NOTE_TYPES = (".txt", ".md")
SHEET_TYPES = (".xlsx", ".xlsm", ".csv", ".tsv")
UPLOAD_RULES = {"questionnaire": (SHEET_TYPES, 25_000_000), "note": (NOTE_TYPES, 2_000_000), "template": ((".pptx", ".docx"), 60_000_000)}
UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
EXPORT_KINDS = ("xlsx", "docx", "pptx", "context", "todo")
EXPORT_NAME = re.compile(r"^MRA_[A-Za-z0-9_.-]+\.(xlsx|docx|pptx|md)$")
MIME = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "md": "text/markdown; charset=utf-8"}
TEMPLATE_EXT = {"pptx": ".pptx", "docx": ".docx"}
TEMPLATE_ID = re.compile(r"^(ingram|aws|[0-9a-f]{10})$")
ACTOR_OK = re.compile(r"[^A-Za-z0-9 ._@'-]")


class UiError(Exception):
    """A problem to show the person: status is the HTTP status, payload the JSON body."""

    def __init__(self, status: int, payload: dict):
        self.status, self.payload = status, payload
        super().__init__(str(payload))


def clean_actor(raw: str | None) -> str:
    a = ACTOR_OK.sub("", (raw or "").strip())[:60].strip()
    return a or getpass.getuser()


def find_soffice() -> str | None:
    cand = [os.environ.get("MRA_SOFFICE"), shutil.which("soffice"), shutil.which("libreoffice"),
            "/Applications/LibreOffice.app/Contents/MacOS/soffice"]
    return next((c for c in cand if c and (Path(c).is_file() or shutil.which(c))), None)


def _b64(s: str, what: str, limit: int) -> bytes:
    try:
        data = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError, TypeError) as e:
        raise UiError(400, {"error": f"{what} is not valid base64 data"}) from e
    if len(data) > limit:
        raise UiError(413, {"error": f"{what} is larger than {limit // 1_000_000} MB"})
    return data


def _excerpt(text: str, quote: str, width: int = 220) -> dict:
    """The quote, found in the note with its surroundings, so a reviewer sees it in context."""
    words = quote.split()
    m = re.search(r"\s+".join(re.escape(w) for w in words), text, re.I) if words else None
    if not m:
        return {"found": False, "before": "", "quote": quote, "after": ""}
    start, end = max(0, m.start() - width), m.end() + width
    return {"found": True, "before": ("\u2026" if start > 0 else "") + text[start:m.start()], "quote": text[m.start():m.end()],
            "after": text[m.end():end] + ("\u2026" if end < len(text) else "")}


class UiApi:
    def __init__(self, svc: AssessmentService, max_upload: int = 40_000_000, hosted: bool = False):
        self.svc, self.lock, self.max_upload, self.hosted = svc, threading.RLock(), max_upload, hosted

    # ---- helpers -----------------------------------------------------------
    def _sid(self, sid: str) -> str:
        if not SET_ID.match(sid or ""):
            raise UiError(400, {"error": "invalid run id"})
        return sid

    # templates are kept in the same storage as the runs. PowerPoint decks: one entry per distinct deck (id = first 10 hex of its SHA-256),
    # plus the name of the preferred one. The built-in default is always there and cannot be removed. Word: at most one template.
    def _tpl_key(self, kind: str, what: str) -> str:
        return f"ui/templates/{kind}.{what}"

    def _pk(self, tid: str, what: str) -> str:
        return f"ui/templates/pptx/{tid}.{what}"

    def _migrate_legacy(self) -> None:
        """The first page version stored a single deck at ui/templates/pptx.bin: keep it as an ordinary uploaded deck."""
        s = self.svc.s
        if s.exists(self._tpl_key("pptx", "bin")):
            name = s.get(self._tpl_key("pptx", "name")).decode() if s.exists(self._tpl_key("pptx", "name")) else "deck.pptx"
            try:
                entry = self._store_pptx(s.get(self._tpl_key("pptx", "bin")), name)
                s.put(self._pk("preferred", "txt"), entry["id"].encode())
            except UiError:
                pass
            s.delete_prefix(self._tpl_key("pptx", "bin"))
            s.delete_prefix(self._tpl_key("pptx", "name"))

    def _store_pptx(self, data: bytes, name: str) -> dict:
        try:
            cov = coverage(data, self.svc.cfg)
        except ValueError as e:
            raise UiError(422, {"error": str(e)}) from e
        tid = hashlib.sha256(data).hexdigest()[:10]
        entry = {"id": tid, "name": name, "size": len(data), "builtin": False, "coverage": cov}
        self.svc.s.put(self._pk(tid, "bin"), data)
        self.svc.s.put(self._pk(tid, "json"), json.dumps(entry).encode())
        return entry

    def list_templates(self) -> dict:
        with self.lock:
            self._migrate_legacy()
            s = self.svc.s
            pref = s.get(self._pk("preferred", "txt")).decode() if s.exists(self._pk("preferred", "txt")) else "ingram"
            uploaded = [json.loads(s.get(k)) for k in s.list("ui/templates/pptx/") if k.endswith(".json")]
            uploaded.sort(key=lambda e: e["name"].lower())
            if pref not in {"ingram", "aws"} and pref not in {e["id"] for e in uploaded}:
                pref = "ingram"
            builtin = [
                {"id": "ingram", "name": INGRAM_LABEL, "size": len(ingram_template_bytes()), "builtin": True, "coverage": None,
                 "description": "Ingram Micro's MAP deliverable template, as supplied. The MRA slides are filled; sections that need other "
                                "inputs (discovery, 7R, cost) stay in the deck, blank."},
                {"id": "aws", "name": DEFAULT_LABEL, "size": len(default_template_bytes()), "builtin": True, "coverage": None,
                 "description": "The AWS MRA readout deck: every part can be filled."}]
            return {"templates": [*builtin, *uploaded], "preferred": pref}

    def _resolve_pptx(self, tid: str | None) -> tuple[bytes | None, str]:
        """The deck to fill: None means the built-in default."""
        listing = self.list_templates()
        tid = tid or listing["preferred"]
        if not TEMPLATE_ID.match(str(tid)):
            raise UiError(400, {"error": "unknown deck template"})
        if tid == "ingram":
            return None, INGRAM_LABEL
        if tid == "aws":
            return default_template_bytes(), DEFAULT_LABEL
        entry = next((e for e in listing["templates"] if e["id"] == tid), None)
        if entry is None:
            raise UiError(404, {"error": "that deck template is no longer stored; choose another"})
        return self.svc.s.get(self._pk(tid, "bin")), entry["name"]

    def _templates(self, tid: str | None = None) -> tuple[dict[str, bytes], dict[str, str]]:
        tpl: dict[str, bytes] = {}
        names: dict[str, str] = {}
        data, name = self._resolve_pptx(tid)
        if data is not None:
            tpl["pptx"], names["pptx"] = data, name
        if self.svc.s.exists(self._tpl_key("docx", "bin")):
            tpl["docx"] = self.svc.s.get(self._tpl_key("docx", "bin"))
            names["docx"] = self.svc.s.get(self._tpl_key("docx", "name")).decode()
        return tpl, names

    def _exports(self, sid: str) -> list[dict]:
        out = []
        for key in self.svc.s.list(f"runs/{sid}/exports/"):
            name = key.rsplit("/", 1)[1]
            if EXPORT_NAME.match(name):
                out.append({"name": name, "size": len(self.svc.s.get(key)), "kind": name.rsplit(".", 1)[1],
                            "url": f"/api/runs/{sid}/exports/{name}"})
        return out

    # ---- information ---------------------------------------------------------
    def info(self) -> dict:
        with self.lock:
            st = self.svc.cfg_settings
            docx = self.svc.s.get(self._tpl_key("docx", "name")).decode() if self.svc.s.exists(self._tpl_key("docx", "name")) else None
            where = f"s3://{st.bucket}/" if st.storage_mode == "aws" else str(Path(st.local_root).resolve())
            return {"version": __version__, "engine": self.svc.llm.name, "model_id": self.svc.llm.model_id, "storage": st.storage_mode,
                    "storage_where": where, "config_hash": self.svc.cfg["config_hash"], "placeholder_notes": self.svc.cfg["placeholder_notes"],
                    "templates": {"pptx": self.list_templates(), "docx": docx}, "libreoffice": (not self.hosted) and find_soffice() is not None,
                    "default_actor": getpass.getuser(), "hosted": self.hosted,
                    "meta_fields": list(META_FIELDS)}

    def list_runs(self) -> dict:
        with self.lock:
            ids = sorted({k.split("/")[1] for k in self.svc.s.list("runs/") if k.count("/") >= 2})
            rows = []
            for sid in ids:
                try:
                    res, state = self.svc.result(sid), self.svc._state(sid) or wf.new_state(sid)
                except KeyError:
                    continue
                rows.append({"set_id": sid, "customer": res["meta"]["customer_label"], "generated_at": res["meta"].get("generated_at"),
                             "final": bool(state["final"]), "blank_ratings": len(res["blank_ratings"]),
                             "engine": res["meta"].get("llm", {}).get("engine")})
            rows.sort(key=lambda r: r["generated_at"] or "", reverse=True)
            return {"runs": rows}

    # ---- uploads: a file goes to storage directly (a presigned form on S3) or, locally, through one PUT -------------------------------------
    def start_upload(self, b: dict, actor: str) -> dict:
        purpose = b.get("purpose")
        rule = UPLOAD_RULES.get(purpose)
        if rule is None:
            raise UiError(400, {"error": "upload purpose must be questionnaire, note or template"})
        exts, limit = rule
        name = Path(str(b.get("name", ""))).name
        if Path(name).suffix.lower() not in exts:
            raise UiError(400, {"error": f"a {purpose} file must be {', '.join(exts)}, not '{Path(name).suffix}'"})
        if isinstance(b.get("size"), int) and b["size"] > limit:
            raise UiError(413, {"error": f"that file is larger than {limit // 1_000_000} MB"})
        uid = secrets.token_hex(16)
        with self.lock:
            self.svc.s.put(f"uploads/{uid}.meta", json.dumps({"name": name, "purpose": purpose, "actor": actor, "limit": limit}).encode())
        presign = getattr(self.svc.s, "presign_post", None)
        if presign is not None:
            return {"upload_id": uid, "mode": "post", **presign(f"uploads/{uid}", limit)}
        return {"upload_id": uid, "mode": "put", "url": f"/api/uploads/{uid}/content", "limit": limit}

    def _upload_meta(self, uid: str, actor: str) -> dict:
        if not UPLOAD_ID.match(str(uid)) or not self.svc.s.exists(f"uploads/{uid}.meta"):
            raise UiError(404, {"error": "that upload was not found or has expired: choose the file again"})
        meta = json.loads(self.svc.s.get(f"uploads/{uid}.meta"))
        if self.hosted and meta["actor"] != actor:
            raise UiError(404, {"error": "that upload was not found or has expired: choose the file again"})
        return meta

    def put_upload(self, uid: str, data: bytes, actor: str) -> None:
        """Local mode: the page sends the file bytes here."""
        with self.lock:
            meta = self._upload_meta(uid, actor)
            if len(data) > meta["limit"]:
                raise UiError(413, {"error": f"that file is larger than {meta['limit'] // 1_000_000} MB"})
            self.svc.s.put(f"uploads/{uid}", data)

    def _take_upload(self, uid: str, purpose: str, actor: str) -> tuple[bytes, str]:
        """Read an uploaded file once and remove it. The size is checked here, whatever the storage enforced."""
        with self.lock:
            meta = self._upload_meta(uid, actor)
            if meta["purpose"] != purpose:
                raise UiError(400, {"error": f"that upload is a {meta['purpose']}, not a {purpose}"})
            if not self.svc.s.exists(f"uploads/{uid}"):
                raise UiError(400, {"error": "the file was not uploaded: choose it again"})
            if self.svc.s.size(f"uploads/{uid}") > meta["limit"]:
                self.svc.s.delete_prefix(f"uploads/{uid}")
                raise UiError(413, {"error": f"that file is larger than {meta['limit'] // 1_000_000} MB"})
            data = self.svc.s.get(f"uploads/{uid}")
            self.svc.s.delete_prefix(f"uploads/{uid}")
            return data, meta["name"]

    def _file_arg(self, b: dict, prefix: str, purpose: str, actor: str, what: str, limit: int) -> tuple[bytes, str] | None:
        """A file given either as an upload id or as inline base64; None when neither is present."""
        if b.get(f"{prefix}upload_id"):
            return self._take_upload(b[f"{prefix}upload_id"], purpose, actor)
        if b.get(f"{prefix}file_b64"):
            return _b64(b[f"{prefix}file_b64"], what, limit), Path(str(b.get(f"{prefix}file_name", ""))).name
        return None

    # ---- create or re-run: a fast part (check the files) and a slow part (score, with the model) -----------------------------------------
    def prepare_run(self, body: dict, actor: str) -> dict:
        """Validate and read everything; raises for any problem. Returns what execute_run needs (small, JSON-safe)."""
        got = self._file_arg(body, "", "questionnaire", actor, "the questionnaire", self.max_upload)
        if got is None or not (body.get("file_name") or got[1]):
            raise UiError(400, {"error": "choose the filled-in questionnaire file first"})
        data, up_name = got
        fname = Path(str(body.get("file_name") or up_name)).name
        if Path(fname).suffix.lower() not in SHEET_TYPES:
            raise UiError(400, {"error": f"the questionnaire must be {', '.join(SHEET_TYPES)}, not '{Path(fname).suffix}'"})
        set_id = (body.get("set_id") or "").strip() or None
        if set_id and not SET_ID.match(set_id):
            raise UiError(400, {"error": "the run id must be 1-64 letters, digits, '.', '_' or '-' and start with a letter or digit"})
        notes = body.get("notes") or []
        if len(notes) > MAX_NOTES:
            raise UiError(400, {"error": f"at most {MAX_NOTES} notes at a time"})
        bad = [n.get("name") for n in notes if Path(str(n.get("name", ""))).suffix.lower() not in NOTE_TYPES]
        if bad:
            raise UiError(400, {"error": f"notes must be .txt or .md files; not accepted: {', '.join(map(str, bad[:5]))}"})
        names = [Path(str(n["name"])).name for n in notes]
        if len(set(names)) != len(names):
            raise UiError(400, {"error": "two notes have the same file name"})
        texts = []
        for n in notes:
            if n.get("upload_id"):
                texts.append(self._take_upload(n["upload_id"], "note", actor)[0].decode("utf-8", "replace"))
            else:
                texts.append(str(n.get("content", "")))
        meta = {k: str(body.get("meta", {}).get(k, "")).strip() for k in META_FIELDS if str(body.get("meta", {}).get(k, "")).strip()}
        with self.lock, tempfile.TemporaryDirectory() as tmp:
            qpath = Path(tmp) / fname
            qpath.write_bytes(data)
            ndir = None
            if notes:
                ndir = Path(tmp) / "notes"
                ndir.mkdir()
                for nm, text in zip(names, texts):
                    (ndir / nm).write_text(text, encoding="utf-8")
            try:
                rs, hashes = load_input(qpath, self.svc.cfg, ndir, set_id, (body.get("customer") or "").strip() or None, meta)
            except InputValidationError as e:
                raise UiError(422, {"errors": e.errors}) from e
            for nm, text in zip(names, texts):
                hashes[f"notes/{nm}"] = sha256(text.encode("utf-8"))
            state = self.svc._state(rs.set_id)
            if state and state["final"]:
                raise UiError(409, {"error": "this run is finalised: reopen it before uploading again"})
            return {"raw": rs_to_raw(rs), "hashes": hashes}

    def execute_run(self, prep: dict, actor: str) -> dict:
        rs = build_response_set(prep["raw"], self.svc.cfg)
        with self.lock:
            try:
                self.svc.run(rs, actor, prep["hashes"])
            except wf.ReviewLocked as e:
                raise UiError(409, {"error": str(e)}) from e
        return {"set_id": rs.set_id}

    def create_run(self, body: dict, actor: str) -> dict:
        return self.execute_run(self.prepare_run(body, actor), actor)

    # ---- the page's view of one run -------------------------------------------------
    def view(self, sid: str) -> dict:
        sid = self._sid(sid)
        with self.lock:
            try:
                res, state = self.svc.result(sid), self.svc._state(sid) or wf.new_state(sid)
            except KeyError as e:
                raise UiError(404, {"error": f"no run '{sid}'"}) from e
            props = self.svc._proposals(sid)
            cfg = self.svc.cfg
            m = build_report_model(res, state, res["meta"].get("generated_at", ""), props, cfg)
            raw = json.loads(self.svc.s.get(f"runs/{sid}/input/response_set.json"))
            notes = {e["evidence_id"]: e for e in raw["evidence"]}
            qdef = cfg["qmap"]
            questions = [{**{k: q[k] for k in ("question_id", "number", "category", "activity", "score", "original_score", "level_text",
                                               "evidence_status", "traceable", "adjusted", "adjustment", "perspective", "module", "evidence_ids")},
                          "text": qdef[q["question_id"]].text, "levels": list(qdef[q["question_id"]].levels)} for q in res["questions"]]
            proposals = []
            for p in props:
                d = p["payload"]
                ex = _excerpt(notes[d["evidence_id"]]["text"], d["quote"]) if d.get("evidence_id") in notes and d.get("quote") else None
                proposals.append({"id": p["id"], "kind": p["kind"], "status": p["status"], "task": p["task"], "payload": d,
                                  "excerpt": ex, "decision": p["decision"], "note_source": notes.get(d.get("evidence_id"), {}).get("source")})
            agg = ("perspectives", "workstreams")
            return {
                "set_id": sid, "customer": m["customer"], "status": self.svc.status(sid), "report_status": m["status"],
                "status_note": m["status_note"], "details": m["details"], "details_missing": m["details_missing"],
                "engine": res["meta"].get("llm", {}).get("engine"), "generated_at": res["meta"].get("generated_at"),
                **{k: res[k] for k in agg},
                "activities": m["activities"], "questions": questions, "blank_ratings": res["blank_ratings"],
                "validation": m["validation"], "proposals": proposals, "narrative": m["summary"], "focus": res["focus"],
                "traceability": res["traceability"], "text_answers": [t for t in res["text_answers"] if t["included"]],
                "notes": [{"id": k, "source": v.get("source", ""), "text": v["text"][:20000], "applies_to": v.get("applies_to", [])}
                          for k, v in notes.items()],
                "input_warnings": m["input_warnings"], "placeholder_notes": res["meta"]["placeholder_notes"],
                "exports": self._exports(sid), "kpis": self.svc.kpis(sid), "audit": self.svc.verify_audit(sid),
                "workstream_names": {w["id"]: w["name"] for w in res["workstreams"]},
            }

    # ---- review actions: each is one service call, nothing more ---------------------------
    def adjust(self, sid: str, b: dict, actor: str) -> None:
        with self.lock:
            self.svc.adjust(self._sid(sid), str(b.get("question", "")), b.get("score"), str(b.get("reason", "")), actor)

    def decide(self, sid: str, b: dict, actor: str) -> None:
        with self.lock:
            self.svc.decide(self._sid(sid), str(b.get("activity", "")), str(b.get("decision", "")), actor, b.get("text") or None,
                            b.get("owner") or None, b.get("due_date") or None, b.get("comment") or None)

    def resolve(self, sid: str, b: dict, actor: str) -> None:
        with self.lock:
            self.svc.resolve(self._sid(sid), str(b.get("item", "")), str(b.get("resolution", "")), str(b.get("note", "")), actor)

    def meta(self, sid: str, b: dict, actor: str) -> None:
        with self.lock:
            self.svc.set_meta(self._sid(sid), {k: b.get(k) for k in META_FIELDS}, actor)

    def confirm(self, sid: str, actor: str) -> None:
        with self.lock:
            self.svc.confirm_scores(self._sid(sid), actor)

    def finalise(self, sid: str, actor: str) -> None:
        with self.lock:
            self.svc.finalise(self._sid(sid), actor)

    def reopen(self, sid: str, b: dict, actor: str) -> None:
        with self.lock:
            self.svc.reopen(self._sid(sid), actor, str(b.get("reason", "")))

    def check_agent(self, sid: str, b: dict) -> None:
        """The quick checks before a long agent task is queued."""
        if str(b.get("task", "")) not in TASKS:
            raise UiError(400, {"error": f"unknown agent task '{b.get('task')}' (available: {', '.join(TASKS)})"})
        with self.lock:
            try:
                self.svc.result(self._sid(sid))
            except KeyError as e:
                raise UiError(404, {"error": f"no run '{sid}'"}) from e

    def agent(self, sid: str, b: dict, actor: str) -> dict:
        with self.lock:
            out = self.svc.agent_task(self._sid(sid), str(b.get("task", "")), actor)
        return {k: out[k] for k in ("stop_reason", "steps", "tool_calls", "input_tokens", "output_tokens", "summary", "proposal_ids")}

    def decide_proposals(self, sid: str, b: dict, actor: str) -> dict:
        d = [(i, "accept", b.get("comment")) for i in b.get("accept", [])] + [(i, "reject", b.get("comment")) for i in b.get("reject", [])]
        if not d:
            raise UiError(400, {"error": "choose at least one proposal"})
        with self.lock:
            return self.svc.decide_proposals(self._sid(sid), d, actor)

    def purge(self, sid: str, b: dict, actor: str) -> dict:
        if b.get("confirm") != self._sid(sid):
            raise UiError(400, {"error": "type the run id to confirm the deletion"})
        with self.lock:
            return self.svc.purge(sid, actor, True)

    # ---- templates ---------------------------------------------------------------------------------------------------
    def upload_template(self, b: dict, actor: str = "") -> dict:
        kind = b.get("kind")
        if kind not in TEMPLATE_EXT:
            raise UiError(400, {"error": "template kind must be pptx or docx"})
        name = Path(str(b.get("file_name", ""))).name
        if Path(name).suffix.lower() != TEMPLATE_EXT[kind]:
            raise UiError(400, {"error": f"a {kind} template must be a {TEMPLATE_EXT[kind]} file"})
        got = self._file_arg(b, "", "template", actor, "the template", UPLOAD_RULES["template"][1])
        if got is None:
            raise UiError(400, {"error": "choose a file first"})
        data = got[0]
        if kind == "pptx":
            with self.lock:
                entry = self._store_pptx(data, name)
                if b.get("prefer"):
                    self.svc.s.put(self._pk("preferred", "txt"), entry["id"].encode())
            return {"name": name, "id": entry["id"], "coverage": entry["coverage"], "warnings": entry["coverage"]["warnings"]}
        self._check_docx(data)
        with self.lock:
            self.svc.s.put(self._tpl_key("docx", "bin"), data)
            self.svc.s.put(self._tpl_key("docx", "name"), name.encode())
        return {"name": name, "id": "docx", "coverage": None, "warnings": []}

    def prefer_template(self, tid: str) -> dict:
        with self.lock:
            if not TEMPLATE_ID.match(tid) or (tid not in ("ingram", "aws") and not self.svc.s.exists(self._pk(tid, "bin"))):
                raise UiError(404, {"error": "no such deck template"})
            self.svc.s.put(self._pk("preferred", "txt"), tid.encode())
        return self.list_templates()

    def delete_template(self, tid: str) -> dict:
        if tid in ("ingram", "aws"):
            raise UiError(400, {"error": "the built-in formats cannot be removed"})
        with self.lock:
            if not TEMPLATE_ID.match(tid) or not self.svc.s.exists(self._pk(tid, "bin")):
                raise UiError(404, {"error": "no such deck template"})
            self.svc.s.delete_prefix(self._pk(tid, "bin"))
            self.svc.s.delete_prefix(self._pk(tid, "json"))
        return self.list_templates()

    def load_template_file(self, kind: str, path: str) -> list[str]:
        """Store a template given as a file path (the --pptx-template launch option) and prefer it. Returns the coverage warnings."""
        f = Path(path).expanduser()
        if kind not in TEMPLATE_EXT or not f.is_file():
            raise UiError(400, {"error": f"the {kind} template file '{path}' was not found"})
        if f.suffix.lower() != TEMPLATE_EXT[kind]:
            raise UiError(400, {"error": f"a {kind} template must be a {TEMPLATE_EXT[kind]} file"})
        data = f.read_bytes()
        if kind == "docx":
            self._check_docx(data)
            with self.lock:
                self.svc.s.put(self._tpl_key("docx", "bin"), data)
                self.svc.s.put(self._tpl_key("docx", "name"), f.name.encode())
            return []
        with self.lock:
            entry = self._store_pptx(data, f.name)
            self.svc.s.put(self._pk("preferred", "txt"), entry["id"].encode())
        return entry["coverage"]["warnings"]

    @staticmethod
    def _check_docx(data: bytes) -> None:
        try:
            from docx import Document
            Document(io.BytesIO(data))
        except Exception as e:  # not a readable Word file
            raise UiError(422, {"error": f"that file cannot be opened as a docx: {type(e).__name__}"}) from e

    # ---- exports and previews ------------------------------------------------------------------------------------------------
    def export(self, sid: str, b: dict, actor: str) -> dict:
        sid = self._sid(sid)
        kinds = [k for k in b.get("kinds", []) if k in EXPORT_KINDS]
        if not kinds:
            raise UiError(400, {"error": "choose what to create"})
        if "pptx" in kinds and "todo" not in kinds:
            kinds.append("todo")
        with self.lock:
            tpl, names = self._templates(b.get("template"))
            self.svc.export(sid, kinds, actor, tpl, names)
            return {"files": self._exports(sid), "deck_template": names.get("pptx", INGRAM_LABEL)}

    def download(self, sid: str, name: str) -> tuple[bytes, str]:
        sid = self._sid(sid)
        if not EXPORT_NAME.match(name):
            raise UiError(404, {"error": "no such file"})
        with self.lock:
            key = f"runs/{sid}/exports/{name}"
            if not self.svc.s.exists(key):
                raise UiError(404, {"error": "no such file"})
            return self.svc.s.get(key), MIME[name.rsplit(".", 1)[1]]

    def download_link(self, sid: str, name: str) -> dict:
        """Hosted: a short-lived presigned S3 link. Local: the page's own download address (the page adds its token)."""
        sid = self._sid(sid)
        if not EXPORT_NAME.match(name):
            raise UiError(404, {"error": "no such file"})
        with self.lock:
            key = f"runs/{sid}/exports/{name}"
            if not self.svc.s.exists(key):
                raise UiError(404, {"error": "no such file"})
            presign = getattr(self.svc.s, "presign_get", None)
            if presign is not None:
                return {"url": presign(key, name, MIME[name.rsplit(".", 1)[1]], 300), "needs_token": False}
        return {"url": f"/api/runs/{sid}/exports/{name}", "needs_token": True}

    def preview(self, sid: str, b: dict, actor: str) -> dict:
        sid, kind = self._sid(sid), b.get("kind")
        if self.hosted:
            raise UiError(501, {"error": "the preview is not available in the hosted version: download the file and open it in PowerPoint or Word"})
        if kind not in ("pptx", "docx"):
            raise UiError(400, {"error": "preview is for the deck (pptx) or the report (docx)"})
        soffice = find_soffice()
        if soffice is None:
            raise UiError(501, {"error": "LibreOffice is needed for the preview. Install it (brew install --cask libreoffice), "
                                         "or download the file and open it in PowerPoint or Word."})
        with self.lock:
            tpl, names = self._templates(b.get("template"))
            files = self.svc.export(sid, [kind], actor, tpl, names)
        name, data = next(iter(files.items()))
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"preview.{kind}"
            src.write_bytes(data)
            profile = Path(tmp) / "profile"
            try:
                subprocess.run([soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
                               check=True, capture_output=True, timeout=240)
            except subprocess.TimeoutExpired as e:
                raise UiError(504, {"error": "LibreOffice took too long. Close any open LibreOffice window and try again."}) from e
            except subprocess.CalledProcessError as e:
                why = (e.stderr or b"").decode("utf-8", "replace")[:200]
                raise UiError(500, {"error": f"LibreOffice could not convert the file: {why}"}) from e
            pdf = Path(tmp) / "preview.pdf"
            if not pdf.is_file():
                raise UiError(500, {"error": "LibreOffice produced no PDF"})
            with self.lock:
                self.svc.s.put(f"runs/{sid}/preview/{kind}.pdf", pdf.read_bytes())
        return {"url": f"/api/runs/{sid}/preview/{kind}.pdf", "file": name}

    def preview_pdf(self, sid: str, kind: str) -> bytes:
        sid = self._sid(sid)
        key = f"runs/{sid}/preview/{kind}.pdf"
        with self.lock:
            if kind not in ("pptx", "docx") or not self.svc.s.exists(key):
                raise UiError(404, {"error": "no preview yet"})
            return self.svc.s.get(key)

