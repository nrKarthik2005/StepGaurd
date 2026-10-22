"""File management in a temporary sandbox.

workspace/   created files and validation/verification markers   (restorable)
published/   published copies                                     (external, irreversible)
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .base import Environment, ToolError, sha256_bytes


class FileEnvironment(Environment):
    name = "file_store"
    family = "file_management"

    def setup(self) -> None:
        sb = self.sandbox
        self.ws, self.pub = sb / "workspace", sb / "published"
        self.meta = self.ws / ".meta"
        for d in (self.ws, self.pub, self.meta):
            d.mkdir(parents=True, exist_ok=True)
        for t in ("create_file", "validate_file", "publish_file", "verify_publication"):
            self.register(t, getattr(self, "_" + t))

    def _path(self, p):
        if not isinstance(p, str) or not p.startswith("workspace/") or ".." in p:
            raise ToolError("400 invalid path", code=400, hint="create_file")
        return self.sandbox / p

    # ------------------------------------------------------------------ tools
    def _create_file(self, a):
        name, content = a.get("name"), a.get("content")
        if not isinstance(name, str) or "/" in name or ".." in name or not isinstance(content, str):
            raise ToolError("422 invalid name or content", code=422)
        p = self.ws / name
        p.write_bytes((content).encode("utf-8"))
        data = p.read_bytes()
        return {"path": f"workspace/{name}", "bytes": len(data), "sha256": sha256_bytes(data)}

    def _validate_file(self, a):
        p = self._path(a.get("path"))
        if not p.exists():
            raise ToolError("404 file not found", code=404, hint="create_file")
        data = p.read_bytes()
        text = data.decode("utf-8", errors="replace")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        valid = len(lines) >= 1 and "�" not in text
        rec = {"path": a["path"], "sha256": sha256_bytes(data), "lines": len(lines), "valid": valid}
        self.meta.mkdir(parents=True, exist_ok=True)
        (self.meta / "validated.json").write_bytes((json.dumps(rec)).encode("utf-8"))
        return {"valid": valid, "lines": len(lines), "sha256": rec["sha256"]}

    def _publish_file(self, a):
        p = self._path(a.get("path"))
        if not p.exists():
            raise ToolError("404 file not found", code=404, hint="create_file")
        data = p.read_bytes()
        version = len(list(self.pub.glob("*"))) + 1
        dest = self.pub / f"{p.name}.v{version}"
        dest.write_bytes(data)
        self.record_effect("publish_file", a["path"], sha=sha256_bytes(data))
        return {"published_path": f"published/{dest.name}", "version": version}

    def _verify_publication(self, a):
        pp = a.get("published_path")
        if not isinstance(pp, str) or not pp.startswith("published/"):
            raise ToolError("400 invalid published_path", code=400, hint="publish_file")
        dest = self.sandbox / pp
        if not dest.exists():
            raise ToolError("404 published file not found", code=404, hint="publish_file")
        v = self.meta / "validated.json"
        vsha = json.loads(v.read_bytes().decode("utf-8"))["sha256"] if v.exists() else None
        ok = sha256_bytes(dest.read_bytes()) == vsha
        self.meta.mkdir(parents=True, exist_ok=True)
        (self.meta / "verified.json").write_bytes(
            json.dumps({"published_path": pp, "verified": ok}).encode("utf-8"))
        return {"verified": ok}

    # ------------------------------------------------------------------ observation
    def observe(self) -> Dict[str, Any]:
        name = self.task["name"]
        f = self.ws / name
        obs: Dict[str, Any] = {"file_exists": f.exists(), "path": None, "file_sha": None, "file_bytes": 0,
                               "validated": False, "validated_sha": None, "validated_ok": False,
                               "published_count": 0, "published": [], "verified": False}
        if f.exists():
            data = f.read_bytes()
            obs.update(file_sha=sha256_bytes(data), file_bytes=len(data), path=f"workspace/{name}")
        v = self.meta / "validated.json"
        if v.exists():
            vd = json.loads(v.read_bytes().decode("utf-8"))
            obs.update(validated=True, validated_sha=vd.get("sha256"), validated_ok=bool(vd.get("valid")))
        pubs = sorted(self.pub.glob("*"))
        obs["published_count"] = len(pubs)
        obs["published"] = [{"path": f"published/{p.name}", "sha": sha256_bytes(p.read_bytes())} for p in pubs]
        obs["published_path"] = obs["published"][-1]["path"] if pubs else None
        vf = self.meta / "verified.json"
        if vf.exists():
            obs["verified"] = bool(json.loads(vf.read_bytes().decode("utf-8")).get("verified"))
        return obs

    def snapshot(self):
        return self._read_tree(self.ws)

    def restore(self, h):
        self._write_tree(h, self.ws)

    def truth(self):
        return {"workspace": self._read_tree(self.ws), "published": self._read_tree(self.pub)}

    # ------------------------------------------------------------------ fault hooks
    def remove_prerequisite(self) -> str:
        f = self.ws / self.task["name"]
        if f.exists():
            f.unlink()
        return "workspace file deleted"

    def corrupt_effect(self, tool, args, variant, before) -> str:
        if tool == "create_file":
            f = self.ws / str(args.get("name"))
            if variant == "suppress":
                old = (before or {}).get("workspace", {}).get(f"workspace/{args.get('name')}")
                if old is not None:
                    f.write_bytes(old)
                elif f.exists():
                    f.unlink()
                return "create_file reported success but wrote no file"
            data = f.read_bytes()
            if variant == "corrupt":
                f.write_bytes(data[: max(1, len(data) // 2)])
                return "create_file wrote a truncated file"
            f.write_bytes(data.replace(b"a", b"e", 1) if b"a" in data else data + b"!")
            return "create_file changed one character"
        return "no corruption defined"
