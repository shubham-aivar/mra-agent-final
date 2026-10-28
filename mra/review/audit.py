"""Append-only audit log. Each entry carries the hash of the one before it, so edits or deletions in the
middle are detectable. Every entry is created with put_new, so two writers can never overwrite each other."""
from __future__ import annotations

import json
from collections.abc import Callable

from ..loader import canonical, sha256
from ..ports import Storage

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, storage: Storage, set_id: str, clock: Callable[[], str]):
        self.s, self.prefix, self.clock = storage, f"runs/{set_id}/audit/", clock

    def append(self, actor: str, action: str, detail: dict) -> dict:
        for _ in range(5):
            keys = self.s.list(self.prefix)
            prev = json.loads(self.s.get(keys[-1])) if keys else None
            body = {"seq": (prev["seq"] + 1) if prev else 1, "ts": self.clock(), "actor": actor, "action": action,
                    "detail": detail, "prev_hash": prev["hash"] if prev else GENESIS}
            entry = {**body, "hash": sha256(canonical(body))}
            if self.s.put_new(f"{self.prefix}{body['seq']:08d}.json", json.dumps(entry, sort_keys=True).encode()):
                return entry
        raise RuntimeError("could not append to the audit log (another writer kept winning)")

    def entries(self) -> list[dict]:
        return [json.loads(self.s.get(k)) for k in self.s.list(self.prefix)]

    def verify(self, anchor: dict | None = None) -> dict:
        """Check the chain. 'anchor' ({seq, hash}) is a head recorded earlier; it also catches a cut-off tail."""
        prev_hash, entries = GENESIS, self.entries()
        for i, e in enumerate(entries, 1):
            body = {k: e[k] for k in ("seq", "ts", "actor", "action", "detail", "prev_hash")}
            if e["seq"] != i:
                return {"ok": False, "entries": len(entries), "first_bad_seq": i, "problem": "missing or reordered entry"}
            if e["prev_hash"] != prev_hash:
                return {"ok": False, "entries": len(entries), "first_bad_seq": i, "problem": "chain link broken"}
            if sha256(canonical(body)) != e["hash"]:
                return {"ok": False, "entries": len(entries), "first_bad_seq": i, "problem": "entry was altered"}
            prev_hash = e["hash"]
        if anchor and (len(entries) < anchor["seq"] or entries[anchor["seq"] - 1]["hash"] != anchor["hash"]):
            return {"ok": False, "entries": len(entries), "first_bad_seq": anchor["seq"],
                    "problem": "log is shorter than, or differs from, the recorded head"}
        return {"ok": True, "entries": len(entries), "first_bad_seq": None, "problem": None}
