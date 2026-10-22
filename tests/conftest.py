"""Test isolation: every test session gets its own database, results, reports, logs and sandbox
directories, so tests never touch the user's real data."""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="stepguard-tests-"))
os.environ["STEPGUARD_DB_PATH"] = str(_TMP / "test.db")
os.environ["STEPGUARD_RESULTS_DIR"] = str(_TMP / "results")
os.environ["STEPGUARD_REPORTS_DIR"] = str(_TMP / "reports")
os.environ["STEPGUARD_LOG_PATH"] = str(_TMP / "logs" / "stepguard.log")
os.environ["STEPGUARD_SANDBOX_DIR"] = str(_TMP / "sandbox")
os.environ["STEPGUARD_BACKEND_URL"] = "http://127.0.0.1:9"      # never reach a real server
for k in ("STEPGUARD_LLM_BASE_URL", "STEPGUARD_LLM_MODEL", "STEPGUARD_LLM_API_KEY"):
    os.environ.pop(k, None)

import pytest  # noqa: E402

from backend.config import ensure_dirs  # noqa: E402
from backend.core.workflow_engine import load_all  # noqa: E402
from backend.database.connection import init_db  # noqa: E402

ensure_dirs()
init_db()


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    return _TMP


@pytest.fixture(scope="session")
def workflows():
    return load_all(refresh=True)


@pytest.fixture(scope="session")
def order_wf(workflows):
    return workflows["order_processing"]


@pytest.fixture
def order_env(tmp_root):
    from backend.environments.order_env import OrderEnvironment
    from backend.experiments.scenarios import make_task
    task = make_task("order_processing", 0, 0)
    env = OrderEnvironment(task, sandbox_root=tmp_root / "sandbox")
    yield env, task
    env.cleanup()
