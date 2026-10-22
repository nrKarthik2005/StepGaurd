"""Run a paired StepGuard experiment from the command line.

Examples:
  python scripts/run_experiments.py --mode quick
  python scripts/run_experiments.py --mode full --note "pre-registered run"
  python scripts/run_experiments.py --mode quick --workflows order_processing --configs SG_FULL B_INTERCEPTOR
"""
import argparse
import json
import sys

import _bootstrap  # noqa: F401

from backend.config import RESULTS_DIR
from backend.core.models import Configuration
from backend.experiments.report import generate_report
from backend.experiments.runner import ExperimentConfig, MODES, run_experiment

SHORT = {"A_UNGUARDED": "A", "B_INTERCEPTOR": "B", "SG_FULL": "SG", "SG_NO_PRE": "-Pre", "SG_NO_POST": "-Post",
         "SG_NO_CHECKPOINT": "-Ckpt", "SG_UNSELECTIVE_RECOVERY": "-Select"}


def pct(v):
    return "  n/a " if v is None else f"{v*100:5.1f}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=list(MODES), default="quick")
    ap.add_argument("--workflows", nargs="+")
    ap.add_argument("--configs", nargs="+", choices=[c.value for c in Configuration])
    ap.add_argument("--conditions", nargs="+", choices=["clean", "E1", "E2", "E3", "E4", "E5", "E6"])
    ap.add_argument("--templates", type=int, help="override templates per workflow")
    ap.add_argument("--seeds", type=int, nargs="+", help="override parameter seeds")
    ap.add_argument("--read-lag", type=float, default=0.03)
    ap.add_argument("--note", default="")
    ap.add_argument("--no-report", action="store_true")
    a = ap.parse_args()

    cfg = ExperimentConfig(mode=a.mode, read_lag=a.read_lag, note=a.note, n_templates=a.templates, seeds=a.seeds)
    if a.workflows:
        cfg.workflows = a.workflows
    if a.configs:
        cfg.configurations = a.configs
    if a.conditions:
        cfg.faults = [None if c == "clean" else c for c in a.conditions]

    def progress(done, total):
        sys.stdout.write(f"\r  {done}/{total} episodes")
        sys.stdout.flush()

    summary = run_experiment(cfg, progress=progress)
    print()
    print(f"experiment {summary['experiment_id']}  ({summary['analysis']}, spec frozen: {summary['spec_frozen']}, "
          f"{summary['duration_s']} s)")
    cols = ["TCR_clean", "TCR_fault", "PER", "EDR", "RSR", "UAPR", "FPRv", "escalation_rate"]
    print(f"{'config':10s}" + "".join(f"{c[:11]:>12s}" for c in cols) + f"{'dup':>6s}")
    for conf, m in summary["metrics"].items():
        print(f"{SHORT.get(conf, conf):10s}" + "".join(f"{pct(m.get(c)):>12s}" for c in cols)
              + f"{m.get('duplicate_side_effects', 0):>6}")
    for h in summary.get("hypotheses", []):
        print(f"  {h['id']}: {'supported' if h['supported'] else 'not supported'} — {h['measured']}")
    print(f"results: {RESULTS_DIR / summary['experiment_id']}")
    if not a.no_report:
        print(f"report:  {generate_report(summary)}")


if __name__ == "__main__":
    main()
