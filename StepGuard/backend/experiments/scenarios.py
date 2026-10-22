"""Task templates and scenario assignment.

A scenario is (workflow, template id, parameter seed, fault condition). Task parameters are
generated deterministically from (family, template, seed), and every configuration receives
exactly the same scenarios, so runs are paired.
"""
from __future__ import annotations

import csv
import hashlib
import io
import random
import zlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend.config import DATA_DIR
from backend.environments.order_env import PRICES

FAULTS = ["E1", "E2", "E3", "E4", "E5", "E6"]
CONDITIONS = [None] + FAULTS

WORDS = ("system network latency budget review release customer audit report quality risk delivery "
         "incident vendor schedule capacity backlog migration security update cost metric target").split()


def stable_seed(*parts: Any) -> int:
    return zlib.crc32("|".join(str(p) for p in parts).encode("utf-8"))


def _sentence(r: random.Random, n: int = 8) -> str:
    return " ".join(r.choice(WORDS) for _ in range(n)).capitalize() + "."


def _sample_rows() -> List[Dict[str, str]]:
    with open(DATA_DIR / "sample.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def make_task(family: str, template: int, seed: int) -> Dict[str, Any]:
    r = random.Random(stable_seed(family, template, seed))
    if family == "order_processing":
        skus = r.sample(sorted(PRICES), r.randint(2, 4))
        items = {s: r.randint(1, 3) for s in skus}
        return {"user": f"user{template:02d}", "items": items,
                "item_list": [{"sku": k, "qty": v} for k, v in items.items()]}
    if family == "document_processing":
        title = f"Quarterly {r.choice(WORDS)} review {template}"
        lines = [f"Title: {title}", "", "## Summary"] + [_sentence(r) for _ in range(r.randint(2, 4))]
        lines += ["", "## Findings"] + [_sentence(r, 10) for _ in range(r.randint(2, 5))]
        lines += ["", "## Actions"] + [f"- {_sentence(r, 6)}" for _ in range(r.randint(1, 4))]
        text = "\n".join(lines) + "\n"
        return {"filename": f"doc_{template:02d}_{seed}.txt", "text": text,
                "text_sha": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    if family == "file_management":
        content = "\n".join(_sentence(r, r.randint(5, 10)) for _ in range(r.randint(3, 8))) + "\n"
        return {"name": f"notes_{template:02d}_{seed}.txt", "content": content,
                "content_sha": hashlib.sha256(content.encode("utf-8")).hexdigest()}
    if family == "data_pipeline":
        rows = r.sample(_sample_rows(), r.randint(12, 30))
        rows = [dict(x) for x in rows]
        for _ in range(r.randint(1, 4)):                      # rows the validator must reject
            bad = dict(r.choice(rows))
            bad["order_id"] = f"X{r.randint(1000, 9999)}"
            choice = r.random()
            if choice < 0.4:
                bad["quantity"] = "-1"
            elif choice < 0.7:
                bad["region"] = ""
            else:
                bad["unit_price"] = "n/a"
            rows.insert(r.randint(0, len(rows)), bad)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        return {"csv": buf.getvalue(), "input_rows": len(rows)}
    raise ValueError(f"unknown family {family}")


@dataclass(frozen=True)
class Scenario:
    workflow: str
    template: int
    seed: int
    fault: Optional[str]

    @property
    def task_id(self) -> str:
        return f"{self.workflow}-t{self.template:02d}-s{self.seed}"

    @property
    def condition(self) -> str:
        return self.fault or "clean"


def build_scenarios(workflows: List[str], n_templates: int, seeds: List[int],
                    conditions: Optional[List[Optional[str]]] = None) -> List[Scenario]:
    conds = CONDITIONS if conditions is None else conditions
    return [Scenario(w, t, s, c) for w in workflows for t in range(n_templates) for s in seeds for c in conds]
