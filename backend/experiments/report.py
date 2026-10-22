"""Generate a reproducible Markdown research report from a finished experiment.

Every number in the report is read from the experiment's stored summary; nothing is typed in.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from backend.config import REPORTS_DIR, RESULTS_DIR
from backend.core.models import CONFIG_DESCRIPTIONS, FAULT_DESCRIPTIONS

METRIC_ROWS = [
    ("TCR_clean", "Task completion, clean runs", True),
    ("TCR_fault", "Task completion, fault-injected runs", True),
    ("PER", "Procedural error rate", True),
    ("EDR", "Error detection rate", True),
    ("RSR", "Recovery success rate", True),
    ("UAPR", "Unsafe action prevention rate", True),
    ("ETO", "Execution time overhead vs A (median, clean)", True),
    ("dTC", "Additional tool executions vs A (mean)", False),
    ("dReads", "Additional state reads vs A (mean)", False),
    ("FPRv", "Verifier false-positive rate (clean)", True),
    ("escalation_rate", "Escalation rate (fault runs)", True),
    ("duplicate_side_effects", "Duplicate irreversible side effects (count)", False),
    ("mean_retries", "Mean retries per episode", False),
    ("recovery_latency_mean", "Mean recovery latency (virtual s)", False),
    ("verification_ms_per_step", "Verifier CPU time per step (ms)", False),
]
SHORT = {"A_UNGUARDED": "A", "B_INTERCEPTOR": "B", "SG_FULL": "SG", "SG_NO_PRE": "−Pre", "SG_NO_POST": "−Post",
         "SG_NO_CHECKPOINT": "−Ckpt", "SG_UNSELECTIVE_RECOVERY": "−Select"}


def _fmt(v, pct: bool) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, (list, tuple)):
        return "n/a" if v[0] is None else f"[{v[0]*100:.1f}, {v[1]*100:.1f}]"
    if pct:
        return f"{v*100:.1f}%"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def build_markdown(summary: Dict) -> str:
    m = summary["metrics"]
    cfgs = list(m)
    cfg = summary.get("config", {})
    lines = [f"# StepGuard experiment report — {summary['experiment_id']}", "",
             f"*Generated from stored results. Analysis type: **{summary.get('analysis', 'exploratory')}**.*", "",
             "## Run configuration", "",
             f"- Mode: `{summary.get('mode')}`; episodes: {summary.get('n_episodes')}; duration: {summary.get('duration_s')} s",
             f"- Workflows: {', '.join(cfg.get('workflows', []))}",
             f"- Templates per workflow: {cfg.get('n_templates')}; parameter seeds: {cfg.get('seeds')}",
             f"- Conditions: {', '.join('clean' if f is None else f for f in cfg.get('faults', []))}",
             f"- Read-after-write lag probability: {cfg.get('read_lag')}",
             f"- Agent: rule-based simulator {cfg.get('agent')}",
             f"- Budgets: {cfg.get('budgets')}",
             f"- Specification frozen during run: {summary.get('spec_frozen')}", "",
             "| Workflow | Specification SHA-256 |", "|---|---|"]
    for w, h in (summary.get("spec_hashes") or {}).items():
        lines.append(f"| {w} | `{h[:16]}…` |")
    lines += ["", "## Configurations", "", "| Code | Configuration | Description |", "|---|---|---|"]
    for c in cfgs:
        lines.append(f"| {SHORT.get(c, c)} | `{c}` | {CONFIG_DESCRIPTIONS.get(c, '')} |")
    lines += ["", "## Metrics (measured)", "", "| Metric | " + " | ".join(SHORT.get(c, c) for c in cfgs) + " |",
              "|---|" + "---|" * len(cfgs)]
    for key, label, pct in METRIC_ROWS:
        lines.append(f"| {label} | " + " | ".join(_fmt(m[c].get(key), pct) for c in cfgs) + " |")
    lines.append("| TCR (faults) 95% Wilson CI | " + " | ".join(_fmt(m[c].get("TCR_fault_ci"), True) for c in cfgs) + " |")
    lines += ["", "EDR, RSR and FPRv are not defined for the unguarded agent, which has no verifier.",
              "ETO uses a virtual cost model (agent step 1.0 s, tool call 0.15 s, state read 0.05 s, snapshot 0.01 s); "
              "the verifier's real CPU time is reported separately.", "",
              "## Task completion by condition", "", "| Condition | " + " | ".join(SHORT.get(c, c) for c in cfgs) + " |",
              "|---|" + "---|" * len(cfgs)]
    for r in summary["per_condition"]:
        cond = r["condition"]
        lines.append(f"| {cond} | " + " | ".join(_fmt(r.get(c), True) for c in cfgs) + " |")
    lines += ["", "| Fault | Description |", "|---|---|"]
    for k, v in FAULT_DESCRIPTIONS.items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## Task completion under faults by workflow", "",
              "| Workflow | " + " | ".join(SHORT.get(c, c) for c in cfgs) + " |", "|---|" + "---|" * len(cfgs)]
    for r in summary["per_family"]:
        lines.append(f"| {r['family']} | " + " | ".join(_fmt(r.get(c), True) for c in cfgs) + " |")
    lines += ["", "## Paired comparisons (completion under faults)", "",
              "| Comparison | Pairs | SG rate | Other rate | Difference | 95% cluster-bootstrap CI | SG-only / other-only | McNemar p | Holm p | Cohen's h |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for k, v in summary["comparisons"].items():
        ci = "n/a" if v["ci_low"] is None else f"[{v['ci_low']*100:.1f}, {v['ci_high']*100:.1f}] pp"
        lines.append(f"| {k.replace('_vs_', ' vs ')} | {v['n_pairs']} | {v['rate_first']*100:.1f}% | "
                     f"{v['rate_second']*100:.1f}% | {v['difference']*100:+.1f} pp | {ci} | {v['only_first']} / "
                     f"{v['only_second']} | {v['mcnemar_p']:.2g} | {v['holm_p']:.2g} | {v['cohens_h']:.2f} |")
    lines += ["", "## Hypotheses from the paper, checked against these measurements", "",
              "| ID | Hypothesis | Measured | Supported in this run |", "|---|---|---|---|"]
    for h in summary.get("hypotheses", []):
        lines.append(f"| {h['id']} | {h['statement']} | {h['measured']} | {'yes' if h['supported'] else 'no'} |")
    lines += ["", "## How to read these results", "",
              "- The agent is a seeded rule-based simulator, not an LLM. Its error and self-correction rates are "
              "modelling assumptions.",
              "- Tools run in local sandboxes; faults are injected deterministically. Contracts were written by the "
              "same author as the simulator and the faults, which favours StepGuard.",
              "- Some E4 variants are deliberately invisible to the contracts (a swapped revenue pair, a wrong "
              "action-item count). StepGuard cannot detect what its contracts do not express.",
              "- SG_NO_POST has no effect probe, so a timed-out non-idempotent call stays in the in-flight log and "
              "later attempts are blocked. This prevents duplicates but can lower completion on E6 below the "
              "unguarded agent.",
              "- The rule-based agent occasionally skips or repeats a step by design (p_skip 0.04, p_dup 0.02), so the "
              "unguarded agent can fail clean runs; duplicate counts for A and B include these natural repeats.",
              f"- This run is labelled **{summary.get('analysis', 'exploratory')}**. The label is mechanical: `full` mode "
              "with workflow specifications unchanged during the run. It does not make this a pre-registered study: "
              "the contracts, agent and harness were developed with exploratory runs drawn from the same task "
              "generator, so these numbers describe the prototype on its own benchmark.",
              "", f"Raw data: `results/{summary['experiment_id']}/` (traces.jsonl, episodes.csv, summary.json).", ""]
    return "\n".join(lines)


def generate_report(summary: Dict, out_dir: Optional[Path] = None) -> Path:
    out = Path(out_dir or REPORTS_DIR)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{summary['experiment_id']}.md"
    path.write_text(build_markdown(summary), encoding="utf-8")
    return path


def load_summary_file(experiment_id: str) -> Dict:
    import json
    p = RESULTS_DIR / experiment_id / "summary.json"
    if not p.exists():
        raise KeyError(f"no summary for experiment {experiment_id}")
    return json.loads(p.read_text(encoding="utf-8"))
