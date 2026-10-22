"""Central configuration. Values can be overridden with environment variables or a .env file."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional at import time
    load_dotenv = None

ROOT = Path(__file__).resolve().parent.parent
if load_dotenv is not None:
    load_dotenv(ROOT / ".env")


def _path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    p = Path(value) if value else default
    return p if p.is_absolute() else (ROOT / p)


WORKFLOW_DIR = ROOT / "backend" / "workflows"
DATA_DIR = ROOT / "data"
RESULTS_DIR = _path("STEPGUARD_RESULTS_DIR", ROOT / "results")
REPORTS_DIR = _path("STEPGUARD_REPORTS_DIR", ROOT / "reports")
DB_PATH = _path("STEPGUARD_DB_PATH", ROOT / "stepguard.db")
LOG_PATH = _path("STEPGUARD_LOG_PATH", ROOT / "logs" / "stepguard.log")
SANDBOX_DIR = _path("STEPGUARD_SANDBOX_DIR", ROOT / ".sandbox")
BACKEND_URL = os.getenv("STEPGUARD_BACKEND_URL", "http://127.0.0.1:8000")

# Virtual cost model (seconds) used for the execution-time-overhead metric.
# Tools in the simulation run in microseconds, so wall-clock time alone would make
# any verification look expensive. Each operation is charged a fixed simulated latency;
# the real CPU time spent inside the verifier is measured and reported separately.
COST_AGENT_STEP = float(os.getenv("STEPGUARD_COST_AGENT_STEP", "1.0"))
COST_TOOL_CALL = float(os.getenv("STEPGUARD_COST_TOOL_CALL", "0.15"))
COST_STATE_READ = float(os.getenv("STEPGUARD_COST_STATE_READ", "0.05"))
COST_SNAPSHOT = float(os.getenv("STEPGUARD_COST_SNAPSHOT", "0.01"))
COST_BACKOFF = float(os.getenv("STEPGUARD_COST_BACKOFF", "0.5"))

# Optional LLM adapter (OpenAI-compatible chat completions endpoint, e.g. Ollama).
LLM_BASE_URL = os.getenv("STEPGUARD_LLM_BASE_URL", "")
LLM_MODEL = os.getenv("STEPGUARD_LLM_MODEL", "")
LLM_API_KEY = os.getenv("STEPGUARD_LLM_API_KEY", "")


def ensure_dirs() -> None:
    for d in (RESULTS_DIR, REPORTS_DIR, LOG_PATH.parent, SANDBOX_DIR, DB_PATH.parent):
        d.mkdir(parents=True, exist_ok=True)
