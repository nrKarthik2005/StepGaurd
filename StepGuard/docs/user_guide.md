# User guide

## Install (Windows PowerShell)

```powershell
cd StepGuard
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
copy .env.example .env                # optional
python scripts/initialize_database.py
python -m pytest
```

macOS / Linux: `source .venv/bin/activate` instead of the activation line, `cp` instead of `copy`.

## Start the system

Terminal 1 — API:

```powershell
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/docs for the interactive API documentation.

Terminal 2 — dashboard:

```powershell
streamlit run frontend/app.py
```

Open http://localhost:8501. If the API is not running the dashboard still works; the sidebar shows
"in-process" and the pages call the same code directly. `python run.py all` starts both in one terminal.

## Dashboard pages

* **Overview** — loaded workflows, their contracts, and the latest measured results (if any).
* **Execution Lab** — run one episode: pick the workflow, configuration, fault, task template, seed and stale-read
  probability. You get the outcome, a verdict chart, a timeline and every step with its arguments, output,
  failed predicate, probe and recovery. Tick the comparison box to run the unguarded agent on the same scenario.
* **Runtime Monitor** — filter the stored step events of an experiment by configuration and verdict, inspect an
  episode, read the application log.
* **Recovery Inspector** — the strategies chosen, crossed with probe result and fault, the admissible set at each
  decision, and checkpoint restorations with their verification result.
* **Experiments** — start a quick, standard or full run with chosen workflows, configurations and conditions.
  Runs over 2,000 episodes continue in the background; the list refreshes until they finish.
* **Results** — every metric with confidence intervals, completion by fault and by workflow, paired statistics,
  hypothesis checks, CSV/JSON/JSONL downloads, and the Markdown report.

## Command line

```powershell
python scripts/run_experiments.py --mode quick
python scripts/run_experiments.py --mode full --note "pre-specified run"
python scripts/run_experiments.py --mode quick --workflows order_processing --configs SG_FULL B_INTERCEPTOR --conditions clean E5 E6
python scripts/generate_report.py                 # latest experiment
python scripts/generate_report.py <experiment_id>
```

## API examples (PowerShell)

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod -Method Post http://127.0.0.1:8000/simulations/run -ContentType "application/json" `
  -Body '{"workflow":"order_processing","configuration":"SG_FULL","fault":"E6","template":2}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/experiments/run -ContentType "application/json" `
  -Body '{"mode":"quick"}'
Invoke-RestMethod http://127.0.0.1:8000/metrics/summary
```

| Method | Path | Purpose |
|---|---|---|
| GET | /health | status, workflow count, database path, LLM adapter configured |
| GET | /workflows, /workflows/{name} | workflow summaries; full specification and YAML |
| GET | /experiments | stored experiments |
| POST | /experiments/run | start an experiment (synchronous up to 2,000 episodes, otherwise background) |
| GET | /experiments/{id} | status, configuration, summary, artifact paths |
| GET | /experiments/{id}/trace | step events, filter by episode, configuration, verdict; paginated |
| GET | /experiments/{id}/episodes, /recovery, /export?kind=csv\|json\|jsonl | episodes, recovery data, files |
| POST | /experiments/{id}/report | write and return the Markdown report |
| POST | /simulations/run | one episode |
| GET | /simulations, /simulations/{id} | stored single runs |
| GET | /metrics/summary | metrics of the latest (or given) experiment |
| GET | /logs | tail of the application log |

Errors return JSON `{"detail": "..."}` with 404 for unknown resources and 422 for invalid input.

## Optional LLM agent

Set `STEPGUARD_LLM_BASE_URL` and `STEPGUARD_LLM_MODEL` in `.env` (e.g. a local Ollama server at
`http://localhost:11434/v1`), and `STEPGUARD_LLM_API_KEY` if the endpoint needs one. The Execution Lab then offers
an `llm` agent. The model only proposes JSON actions; StepGuard checks and executes them. Malformed replies are
retried up to three times and then end the episode; network errors end it safely. Without these settings the app
behaves exactly as before.

## Writing a new workflow

Copy a file in `backend/workflows/`, keep `environment` pointing at an implemented environment, and define states,
transitions, `stage_rules`, `invariants`, `completion`, `tools` (contracts), `plan` and `fault_targets`.
Conditions are expressions in the allowlisted language (`obs`, `args`, `task`, `before`, `after`, `output`) or
the name of a registered predicate. The loader rejects the file with a clear message if anything is undefined.

## Troubleshooting

* `Activate.ps1 cannot be loaded` — run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.
* Dashboard says "in-process" — the API is not reachable at `STEPGUARD_BACKEND_URL`; start it or ignore.
* Port in use — `uvicorn backend.main:app --port 8010` and set `STEPGUARD_BACKEND_URL=http://127.0.0.1:8010`.
* Start over — `python scripts/initialize_database.py --reset` and delete `results/` and `reports/` contents.
