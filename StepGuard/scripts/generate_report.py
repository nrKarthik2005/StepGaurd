"""Regenerate the Markdown report for a stored experiment (default: the latest completed one).

Usage:  python scripts/generate_report.py [experiment_id]
"""
import argparse

import _bootstrap  # noqa: F401

from backend.database.repository import Repository
from backend.experiments.report import generate_report, load_summary_file


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("experiment_id", nargs="?")
    a = ap.parse_args()
    if a.experiment_id:
        try:
            summary = load_summary_file(a.experiment_id)
        except KeyError:
            e = Repository().get_experiment(a.experiment_id)
            if not e or not e.get("summary"):
                raise SystemExit(f"no completed experiment '{a.experiment_id}'")
            summary = e["summary"]
    else:
        e = Repository().latest_completed()
        if not e:
            raise SystemExit("no completed experiment yet; run scripts/run_experiments.py first")
        summary = e["summary"]
    print(generate_report(summary))


if __name__ == "__main__":
    main()
