"""CSV data pipeline in a sandbox directory.

source/   input CSV provided by the task
work/     raw.csv, validated.csv, output.csv, verified.json   (restorable)
exports/  exported results                                     (external, irreversible)

transform: revenue per region = sum(quantity * unit_price) over valid rows.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List

from .base import Environment, ToolError, sha256_bytes

REQUIRED_COLUMNS = ["order_id", "region", "quantity", "unit_price"]


def read_csv(text: str) -> List[Dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def valid_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out = []
    for r in rows:
        try:
            q, p = int(r.get("quantity", "")), float(r.get("unit_price", ""))
        except (TypeError, ValueError):
            continue
        if r.get("region") and q > 0 and p >= 0:
            out.append(r)
    return out


def aggregate(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    tot: Dict[str, float] = {}
    cnt: Dict[str, int] = {}
    for r in rows:
        reg = r["region"]
        tot[reg] = tot.get(reg, 0.0) + int(r["quantity"]) * float(r["unit_price"])
        cnt[reg] = cnt.get(reg, 0) + 1
    return [{"region": k, "revenue": round(tot[k], 2), "orders": cnt[k]} for k in sorted(tot)]


def write_csv(rows: List[Dict[str, Any]], fields: List[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


class PipelineEnvironment(Environment):
    name = "csv_pipeline"
    family = "data_pipeline"

    def setup(self) -> None:
        sb = self.sandbox
        self.src, self.work, self.exports = sb / "source", sb / "work", sb / "exports"
        for d in (self.src, self.work, self.exports):
            d.mkdir(parents=True, exist_ok=True)
        (self.src / "input.csv").write_bytes((self.task["csv"]).encode("utf-8"))
        for t in ("load_data", "validate_data", "transform_data", "verify_output", "export_output"):
            self.register(t, getattr(self, "_" + t))

    # ------------------------------------------------------------------ tools
    def _load_data(self, a):
        if a.get("source") != "source/input.csv":
            raise ToolError("404 source not found", code=404)
        text = (self.src / "input.csv").read_bytes().decode("utf-8")
        (self.work / "raw.csv").write_bytes((text).encode("utf-8"))
        rows = read_csv(text)
        return {"path": "work/raw.csv", "rows": len(rows), "columns": list(rows[0].keys()) if rows else []}

    def _validate_data(self, a):
        raw = self.work / "raw.csv"
        if a.get("path") != "work/raw.csv" or not raw.exists():
            raise ToolError("404 raw data not found", code=404, hint="load_data")
        rows = read_csv(raw.read_bytes().decode("utf-8"))
        cols = list(rows[0].keys()) if rows else []
        missing = [c for c in REQUIRED_COLUMNS if c not in cols]
        if missing:
            raise ToolError(f"422 missing columns {missing}", code=422)
        good = valid_rows(rows)
        (self.work / "validated.csv").write_bytes((write_csv(good, REQUIRED_COLUMNS)).encode("utf-8"))
        return {"path": "work/validated.csv", "valid_rows": len(good), "rejected_rows": len(rows) - len(good)}

    def _transform_data(self, a):
        v = self.work / "validated.csv"
        if a.get("path") != "work/validated.csv" or not v.exists():
            raise ToolError("404 validated data not found", code=404, hint="validate_data")
        agg = aggregate(read_csv(v.read_bytes().decode("utf-8")))
        (self.work / "output.csv").write_bytes((write_csv(agg, ["region", "revenue", "orders"])).encode("utf-8"))
        return {"output_path": "work/output.csv", "rows": len(agg)}

    def _verify_output(self, a):
        o = self.work / "output.csv"
        if a.get("output_path") != "work/output.csv" or not o.exists():
            raise ToolError("404 output not found", code=404, hint="transform_data")
        out = read_csv(o.read_bytes().decode("utf-8"))
        val = read_csv((self.work / "validated.csv").read_bytes().decode("utf-8"))
        expected_total = round(sum(int(r["quantity"]) * float(r["unit_price"]) for r in val), 2)
        total = round(sum(float(r["revenue"]) for r in out), 2)
        ok = abs(total - expected_total) < 0.01 and len(out) == len({r["region"] for r in val})
        rec = {"verified": ok, "sha256": sha256_bytes(o.read_bytes()), "total": total}
        (self.work / "verified.json").write_bytes((json.dumps(rec)).encode("utf-8"))
        return {"verified": ok, "checksum": rec["sha256"]}

    def _export_output(self, a):
        o = self.work / "output.csv"
        if a.get("output_path") != "work/output.csv" or not o.exists():
            raise ToolError("404 output not found", code=404, hint="transform_data")
        n = len(list(self.exports.glob("*.csv"))) + 1
        data = o.read_bytes()
        (self.exports / f"export_{n}.csv").write_bytes(data)
        self.record_effect("export_output", a["output_path"], sha=sha256_bytes(data))
        return {"export_id": f"EXP-{n:03d}", "status": "exported"}

    # ------------------------------------------------------------------ observation
    def observe(self) -> Dict[str, Any]:
        obs: Dict[str, Any] = {"raw_exists": False, "raw_rows": 0, "raw_columns": [], "validated_exists": False,
                               "validated_rows": 0, "validated_total": 0.0, "validated_regions": 0,
                               "output_exists": False, "output_rows": 0, "output_total": 0.0,
                               "output_columns": [], "output_sha": None, "output_path": None, "verified": False, "verified_sha": None,
                               "exports": 0, "export_shas": []}
        raw = self.work / "raw.csv"
        if raw.exists():
            rows = read_csv(raw.read_bytes().decode("utf-8"))
            obs.update(raw_exists=True, raw_rows=len(rows), raw_columns=list(rows[0].keys()) if rows else [])
        v = self.work / "validated.csv"
        if v.exists():
            rows = read_csv(v.read_bytes().decode("utf-8"))
            obs.update(validated_exists=True, validated_rows=len(rows),
                       validated_total=round(sum(int(r["quantity"]) * float(r["unit_price"]) for r in rows), 2),
                       validated_regions=len({r["region"] for r in rows}))
        o = self.work / "output.csv"
        if o.exists():
            rows = read_csv(o.read_bytes().decode("utf-8"))
            obs.update(output_exists=True, output_rows=len(rows), output_path="work/output.csv",
                       output_columns=list(rows[0].keys()) if rows else [],
                       output_total=round(sum(float(r["revenue"]) for r in rows), 2) if rows else 0.0,
                       output_sha=sha256_bytes(o.read_bytes()))
        vf = self.work / "verified.json"
        if vf.exists():
            vd = json.loads(vf.read_bytes().decode("utf-8"))
            obs.update(verified=bool(vd.get("verified")), verified_sha=vd.get("sha256"))
        ex = sorted(self.exports.glob("*.csv"))
        obs["exports"] = len(ex)
        obs["export_shas"] = [sha256_bytes(p.read_bytes()) for p in ex]
        return obs

    def snapshot(self):
        return self._read_tree(self.work)

    def restore(self, h):
        self._write_tree(h, self.work)

    def truth(self):
        return {"source": self.task["csv"], "work": self._read_tree(self.work),
                "exports": self._read_tree(self.exports)}

    # ------------------------------------------------------------------ fault hooks
    def remove_prerequisite(self) -> str:
        raw = self.work / "raw.csv"
        if raw.exists():
            raw.unlink()
        return "raw data file deleted"

    def corrupt_effect(self, tool, args, variant, before) -> str:
        if tool == "transform_data":
            o = self.work / "output.csv"
            if variant == "suppress":
                old = (before or {}).get("work", {}).get("work/output.csv")
                if old is not None:
                    o.write_bytes(old)
                elif o.exists():
                    o.unlink()
                return "transform_data returned success without creating output.csv"
            rows = read_csv(o.read_bytes().decode("utf-8"))
            if variant == "corrupt" or len(rows) < 2:
                rows = rows[:-1]
                o.write_bytes((write_csv(rows, ["region", "revenue", "orders"])).encode("utf-8"))
                return "output.csv is missing one region"
            rows[0]["revenue"], rows[1]["revenue"] = rows[1]["revenue"], rows[0]["revenue"]
            o.write_bytes((write_csv(rows, ["region", "revenue", "orders"])).encode("utf-8"))
            return "two regions' revenues were swapped (row count and total unchanged)"
        return "no corruption defined"
