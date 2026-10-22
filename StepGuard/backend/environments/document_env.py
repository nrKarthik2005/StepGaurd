"""Document processing in a sandbox directory.

inbox/     source document provided by the task
working/   document.txt, validation.json, report.json   (restorable)
registry/  submitted reports                             (external, irreversible)
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .base import Environment, ToolError, sha256_bytes

REQUIRED_SECTIONS = ["Summary", "Findings", "Actions"]


def parse_document(text: str) -> Dict[str, Any]:
    lines = text.splitlines()
    title = lines[0].removeprefix("Title:").strip() if lines and lines[0].startswith("Title:") else None
    sections, current = {}, None
    for ln in lines[1:]:
        if ln.startswith("## "):
            current = ln[3:].strip()
            sections[current] = []
        elif current and ln.strip():
            sections[current].append(ln.strip())
    return {"title": title, "sections": sections, "words": len(text.split())}


def build_report(text: str) -> Dict[str, Any]:
    d = parse_document(text)
    return {
        "title": d["title"],
        "sections": [s for s in REQUIRED_SECTIONS if s in d["sections"]],
        "word_count": d["words"],
        "action_items": len(d["sections"].get("Actions", [])),
        "summary": " ".join(d["sections"].get("Summary", []))[:200],
        "source_sha256": sha256_bytes(text.encode()),
    }


class DocumentEnvironment(Environment):
    name = "document_store"
    family = "document_processing"

    def setup(self) -> None:
        sb = self.sandbox
        self.inbox, self.work, self.registry = sb / "inbox", sb / "working", sb / "registry"
        for d in (self.inbox, self.work, self.registry):
            d.mkdir(parents=True, exist_ok=True)
        (self.inbox / self.task["filename"]).write_bytes((self.task["text"]).encode("utf-8"))
        for t in ("load_document", "validate_document", "generate_report", "submit_report"):
            self.register(t, getattr(self, "_" + t))

    @property
    def doc(self):
        return self.work / "document.txt"

    # ------------------------------------------------------------------ tools
    def _load_document(self, a):
        src = self.inbox / str(a.get("filename"))
        if not src.exists():
            raise ToolError("404 source document not found", code=404)
        text = src.read_bytes().decode("utf-8")
        self.doc.write_bytes((text).encode("utf-8"))
        d = parse_document(text)
        return {"doc_id": "document.txt", "title": d["title"], "words": d["words"]}

    def _validate_document(self, a):
        if a.get("doc_id") != "document.txt":
            raise ToolError("400 invalid doc_id", code=400, hint="load_document")
        if not self.doc.exists():
            raise ToolError("404 working document not found", code=404, hint="load_document")
        text = self.doc.read_bytes().decode("utf-8")
        d = parse_document(text)
        issues = [f"missing section {s}" for s in REQUIRED_SECTIONS if s not in d["sections"]]
        if not d["title"]:
            issues.append("missing title")
        result = {"valid": not issues, "issues": issues, "sha256": sha256_bytes(text.encode())}
        (self.work / "validation.json").write_bytes((json.dumps(result)).encode("utf-8"))
        return {"valid": result["valid"], "issues": issues, "sections": list(d["sections"])}

    def _generate_report(self, a):
        if not self.doc.exists():
            raise ToolError("404 working document not found", code=404, hint="load_document")
        text = self.doc.read_bytes().decode("utf-8")
        report = build_report(text)
        (self.work / "report.json").write_bytes((json.dumps(report)).encode("utf-8"))
        return {"report_path": "working/report.json", "word_count": report["word_count"]}

    def _submit_report(self, a):
        rp = self.sandbox / str(a.get("report_path"))
        if a.get("report_path") != "working/report.json" or not rp.exists():
            raise ToolError("404 report not found", code=404, hint="generate_report")
        n = len(list(self.registry.glob("submission_*.json"))) + 1
        data = rp.read_bytes()
        (self.registry / f"submission_{n}.json").write_bytes(data)
        self.record_effect("submit_report", a["report_path"], sha=sha256_bytes(data))
        return {"submission_id": f"SUB-{n:03d}", "status": "submitted"}

    # ------------------------------------------------------------------ observation
    def observe(self) -> Dict[str, Any]:
        obs: Dict[str, Any] = {"doc_loaded": self.doc.exists(), "doc_id": None, "doc_sha": None, "validated": False,
                               "validation_valid": False, "validation_sha": None, "report_exists": False,
                               "report": None, "submissions": 0, "submitted_shas": []}
        if self.doc.exists():
            obs["doc_sha"] = sha256_bytes(self.doc.read_bytes())
            obs["doc_id"] = "document.txt"           # read-only lookup of the resource identifier
        v = self.work / "validation.json"
        if v.exists():
            vd = json.loads(v.read_bytes().decode("utf-8"))
            obs.update(validated=True, validation_valid=bool(vd.get("valid")), validation_sha=vd.get("sha256"))
        r = self.work / "report.json"
        if r.exists():
            obs["report_exists"] = True
            try:
                obs["report"] = json.loads(r.read_bytes().decode("utf-8"))
            except json.JSONDecodeError:
                obs["report"] = {}
        subs = sorted(self.registry.glob("submission_*.json"))
        obs["submissions"] = len(subs)
        obs["submitted_shas"] = [sha256_bytes(p.read_bytes()) for p in subs]
        obs["report_sha"] = sha256_bytes(r.read_bytes()) if r.exists() else None
        obs["report_path"] = "working/report.json" if r.exists() else None
        return obs

    def snapshot(self):
        return self._read_tree(self.work)

    def restore(self, h):
        self._write_tree(h, self.work)

    def truth(self):
        return {"source": self.task["text"], "working": self._read_tree(self.work),
                "registry": self._read_tree(self.registry)}

    # ------------------------------------------------------------------ fault hooks
    def remove_prerequisite(self) -> str:
        if self.doc.exists():
            self.doc.unlink()
        return "working document deleted"

    def corrupt_effect(self, tool, args, variant, before) -> str:
        if tool == "generate_report":
            r = self.work / "report.json"
            if variant == "suppress":
                if before and "working/report.json" in before.get("working", {}):
                    r.write_bytes(before["working"]["working/report.json"])
                elif r.exists():
                    r.unlink()
                return "generate_report reported success but wrote no report"
            rep = json.loads(r.read_bytes().decode("utf-8"))
            if variant == "corrupt":
                rep["sections"] = rep["sections"][:-1]
                r.write_bytes((json.dumps(rep)).encode("utf-8"))
                return "report written without one required section"
            rep["action_items"] = rep["action_items"] + 1      # plausible-looking wrong value
            r.write_bytes((json.dumps(rep)).encode("utf-8"))
            return "report written with a wrong action-item count"
        return "no corruption defined"
