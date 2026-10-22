# Experiment protocol

## Design

Paired, full-factorial simulation. A **scenario** is (workflow, task template, parameter seed, condition), where
the condition is `clean` or one of E1–E6. Every scenario is run once under every configuration:

`A_UNGUARDED, B_INTERCEPTOR, SG_FULL, SG_NO_PRE, SG_NO_POST, SG_NO_CHECKPOINT, SG_UNSELECTIVE_RECOVERY`

All randomness (task parameters, the agent's slips, fault occurrence and variant, stale reads) is drawn from
streams seeded by the scenario, never by the configuration, so configurations differ only in how they react.

| Mode | Templates per workflow | Seeds | Episodes (4 workflows × 7 conditions × 7 configurations) |
|---|---|---|---|
| quick | 5 | 0 | 980 |
| standard | 10 | 0, 1 | 3,920 |
| full | 25 | 0, 1, 2 | 14,700 |

Fixed settings: read-after-write lag probability 0.03; budgets of 25 executions, 10 blocks, 60 proposals and
2 retries per episode; agent slip rates p_skip 0.04, p_dup 0.02, self-correction p_fix 0.5.

## Pre-specified hypotheses

Defined in `backend/experiments/metrics.py` before the full run; each is reported as supported or not.

| ID | Hypothesis |
|---|---|
| H1 | SG's completion under faults exceeds B's by at least 5 percentage points, with the 95% CI excluding 0 |
| H2 | SG reduces the procedural error rate by at least 50% relative to A |
| H3 | SG's median execution-time overhead on clean runs is at most 25% (virtual cost model) |
| H4 | SG's verifier false-positive rate on clean runs is at most 5% |
| H5 | Each ablation completes significantly fewer fault-injected tasks than SG (Holm-adjusted p < 0.05) |

## Confirmatory vs exploratory

* **Confirmatory**: `full` mode, specifications frozen. This is a mechanical label for the run, not a claim that the study was pre-registered: the contracts and harness in this repository were developed with exploratory runs from the same task generator. The SHA-256 of every workflow file is recorded at the
  start and re-checked at the end; any change marks the run exploratory.
* **Exploratory**: every other run, including all runs used while developing contracts, the agent, or the
  environments. Exploratory numbers are for development and must not be quoted as evidence.

Changing a workflow, the agent rates, the fault definitions or the metrics after looking at a confirmatory
run means a new confirmatory run is required, and the change should be reported.

## Procedure

```powershell
python scripts/initialize_database.py
python -m pytest
python scripts/run_experiments.py --mode full --note "pre-specified run"
python scripts/generate_report.py
```

Outputs: `results/<experiment_id>/traces.jsonl` (every step of every episode), `episodes.csv` (one row per
episode), `summary.json` (all metrics and tests), `config.json` (configuration and spec hashes); the SQLite tables;
and `reports/<experiment_id>.md`.

## Reproducibility

The experiment ID is `exp-<UTC timestamp>-<hash of config and spec hashes>`; the hash part is identical for
identical configurations and specifications. Re-running the same mode with the same specifications reproduces the
same episodes exactly (tested in `tests/test_fault_injection.py`), except for wall-clock and CPU timings.

## Reporting rules

* Report every configuration and every metric, including those where StepGuard is not better.
* Report *n/a* for undefined ratios.
* Report the E4 variants that the contracts cannot detect.
* Do not present simulated results as measurements of LLM agents or production services.
