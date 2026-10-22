# StepGuard — runtime verification and state-aware recovery for procedural errors in tool-using agents

StepGuard is a local research prototype. It sits between an agent and its tools, checks every proposed action
against a workflow specification **before** it runs, checks the environment's actual state **after** it runs,
and, when a check fails, chooses a recovery strategy that is provably admissible for that tool (resume, retry,
rollback with compensation, repair, or escalate). It ships with four sandboxed workflows, a deterministic fault
injector, two baselines, four ablations, an independent oracle, a paired experiment runner, a FastAPI backend
and a Streamlit dashboard.

Everything runs offline on Windows, macOS or Linux. No Docker, cloud service, API key or real payment
service is used; file workflows only touch a temporary sandbox that is deleted after every episode.

> **Scope.** The agent in the reported experiments is a seeded rule-based simulator, and the environments, contracts,
> faults and oracle were written by the same author. The results show how the mechanism behaves on this benchmark.
> They are not evidence about LLM agents or production systems.

## Quick start (Windows PowerShell)

```powershell
cd StepGuard
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/initialize_database.py
python -m pytest

# terminal 1
uvicorn backend.main:app --host 127.0.0.1 --port 8000      # API docs: http://127.0.0.1:8000/docs
# terminal 2 (activate .venv first)
streamlit run frontend/app.py                               # dashboard: http://localhost:8501

# a small experiment, then export and report
python scripts/run_experiments.py --mode quick
python scripts/generate_report.py
```

`python run.py` wraps the same commands (`init-db`, `api`, `ui`, `all`, `test`, `experiment`, `report`). If
script activation is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once. Python 3.11+ is
required.

## What is implemented

| Area | Implementation |
|---|---|
| Workflows | Four YAML finite-state workflows (order processing, document processing, file management, CSV data pipeline). The loader validates them with Pydantic and records a SHA-256 hash of each file. |
| Verdicts | ACCEPT, BLOCK, VIOLATE and INCONCLUSIVE, each stored with the stage, failed predicate, probe, admissible strategies, decision, checkpoint, barrier, in-flight keys and timings. |
| Contracts | Roles, dependencies, argument schema, preconditions, output schema, effect and unchanged conditions, idempotency keys, R/C/I reversibility, compensation sized from the observed effect, allowed strategies. |
| Safe expressions | AST allowlist evaluator. YAML can never run arbitrary Python, and an undefined value gives INCONCLUSIVE, never ACCEPT. |
| Checkpoints | Commit only on accepted steps, irreversible barrier, feasibility test, compensation, and restoration verified against stage, invariants and snapshot. |
| Recovery | RESUME, RETRY, ROLLBACK, REPAIR, ESCALATE with admissibility rules, chosen by a selective (failure-class) or unselective policy. |
| Faults | E1 out-of-order, E2 missing prerequisite, E3 malformed output, E4 silent failure (suppress / corrupt / subtle), E5 duplicate replay, E6 timeout before/after the effect. All are seeded and identical across configurations. |
| Comparisons | A unguarded, B schema-checking interceptor with retry/back-off, SG_FULL, SG_NO_PRE, SG_NO_POST, SG_NO_CHECKPOINT, SG_UNSELECTIVE_RECOVERY. |
| Evaluation | Independent oracle; metrics TCR, PER, EDR, RSR, UAPR, ETO, ΔTC, ΔReads, FPRv, escalation rate, duplicates, retries, recovery latency and verifier CPU time; Wilson CIs, cluster bootstrap, exact McNemar with Holm correction, Cohen's h; confirmatory and exploratory labels. |
| Storage | SQLite (10 tables), JSONL traces, CSV, JSON summary, Markdown report. |
| Interfaces | FastAPI with OpenAPI docs; Streamlit dashboard with 6 pages; CLI scripts. |
| Optional LLM agent | OpenAI-compatible adapter configured by environment variables. It only proposes actions, handles malformed replies, never logs the key, and is off by default. |

## Directory structure

```
StepGuard/
├── backend/
│   ├── main.py                 FastAPI app
│   ├── config.py               paths, cost model, env settings
│   ├── api/                    routes.py, schemas.py
│   ├── core/                   models, expressions, workflow_engine, contract_engine, state_observer,
│   │                           tool_executor, runtime_controller, checkpoint_manager, recovery_manager,
│   │                           fault_injector, action_registry, audit_logger
│   ├── environments/           order, document, file, pipeline sandboxes
│   ├── agents/                 base_agent, rule_based_agent, llm_adapter
│   ├── workflows/              4 YAML specifications
│   ├── experiments/            scenarios, baselines, oracle, runner, metrics, statistics, report
│   └── database/               schema.sql, connection.py, repository.py
├── frontend/                   app.py, pages/1_…5_, components/
├── tests/                      9 test modules (111 tests)
├── scripts/                    initialize_database.py, run_experiments.py, generate_report.py
├── data/sample.csv             synthetic input for the data pipeline
├── docs/                       architecture, methodology, experiment_protocol, user_guide
├── results/<experiment_id>/    traces.jsonl, episodes.csv, summary.json, config.json
├── reports/<experiment_id>.md  generated reports
├── requirements.txt  .env.example  .gitignore  pytest.ini  run.py
```

## Results in this repository

`results/exp-20261022-144335-1b1b66c7/` holds one `full` run: 4 workflows × 25 templates × 3 seeds × 7
conditions × 7 configurations = 14,700 episodes. The specifications stayed frozen during the run, so it carries
the "confirmatory" label. See `reports/exp-20261022-144335-1b1b66c7.md` for every metric and test. Headline
measurements from that run:

| | A unguarded | B interceptor | StepGuard |
|---|---|---|---|
| Task completion, clean | 92.0% | 92.0% | 100.0% |
| Task completion, faults (95% CI) | 49.6% [47.2, 51.9] | 49.5% [47.2, 51.8] | 97.8% [97.0, 98.4] |
| Procedural error rate | 25.6% | 19.6% | 0.4% |
| Duplicate irreversible side effects | 480 | 484 | 0 |
| Verifier false-positive rate, clean | n/a | 0.0% | 2.5% |
| Execution-time overhead, clean (virtual cost model) | – | 0.0% | 9.0% |

StepGuard's remaining failures under faults are exactly the E4 "subtle" corruptions that its contracts do not
express (a wrong action-item count in reports, two swapped revenue figures in the pipeline): 87.0% completion on
E4 and 100% on every other fault. Every ablation completed significantly fewer fault-injected tasks than the full
system (Holm-adjusted McNemar). B did no better than A on completion, because it retries timeouts blindly and
duplicates charges; that is how generic retry middleware behaves, not a deliberate weakening.

Re-running `python scripts/run_experiments.py --mode full` reproduces these episodes; the timings will differ.

## Examples (real traces, `order_processing`, template 0)

**Successful execution** — SG_FULL, clean: `authenticate → create_cart → add_item ×4 → checkout → confirm_order`,
all ACCEPT, final stage CONFIRMED.

**Blocked action** — SG_FULL, E1: at step 7 the injected `confirm_order` arrives before checkout and is blocked
(`transition_defined`: "'confirm_order' is not allowed in state ITEM_ADDED; enabled: [...]"). It is never
executed; the agent gets the enabled set as feedback, proposes `checkout`, and the task completes. The unguarded
agent on the same scenario keeps calling `confirm_order` and never completes.

**Silent failure and recovery** — SG_FULL, E4 (corrupt), template 3: `add_item` reports success but the cart
holds the wrong quantity. The post-check returns VIOLATE (`qty_increased`) and the probe reports UNKNOWN (neither
the intended effect nor no change). StepGuard rolls back to the last checkpoint, compensating with `remove_item`
sized from the quantity that actually landed, and verifies the restoration. The agent re-adds the item and the
task completes.

**Timeout after the charge** — SG_FULL, E6 (after): `checkout` times out after charging. The verdict is
INCONCLUSIVE, the consistent-read probe reports APPLIED, and StepGuard RESUMEs, rebuilding the order ID from
state. One charge is made. B_INTERCEPTOR on the same scenario retries the timeout and records 2 duplicate side
effects.

Try these in the dashboard's **Execution Lab**, or call `POST /simulations/run`.

## Tests

`python -m pytest` runs 111 tests in about 5 seconds: workflow loading and validation, the expression sandbox,
pre/post checks and the probe, all four verdicts, checkpoints and verified rollback, recovery admissibility,
deterministic fault injection across all workflows × faults × configurations, metrics and statistics, every API
endpoint and error code, and an end-to-end persisted experiment. Tests run against a temporary database and
directories.

## Limitations

* The agent is a simulator with hand-set slip and self-correction rates. No LLM was evaluated; the LLM adapter is
  tested only with a mocked endpoint.
* Environments are small simulations. The "irreversibility" of checkout, submission, publication and export holds
  inside the simulation only.
* Contracts, faults and oracle share an author, which favours StepGuard. Faults outside what contracts express are
  not detected (see E4 subtle).
* ETO uses a virtual cost model, not measured latency of real services.
* Stale reads are modelled by a single probability (0.03); FPRv depends on it.
* `SG_NO_POST` cannot resolve an in-flight timed-out call (no probe), so it blocks later attempts. This lowers its E6
  completion below A by construction.
* The literature comparison in the accompanying paper is not established by this code. These experiments do not
  show that a research gap exists.

See `docs/` for the architecture, methodology, protocol and user guide, and `docs/screenshots/` for the dashboard.
