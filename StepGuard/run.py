"""StepGuard launcher (works the same in PowerShell, cmd and bash).

  python run.py init-db              create the SQLite database
  python run.py api                  start FastAPI on http://127.0.0.1:8000 (docs at /docs)
  python run.py ui                   start the Streamlit dashboard on http://localhost:8501
  python run.py all                  start both (API in a child process)
  python run.py test                 run the pytest suite
  python run.py experiment [args]    run scripts/run_experiments.py with the given arguments
  python run.py report [id]          regenerate a Markdown report
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
PY = sys.executable


def sh(args):
    return subprocess.call(args, cwd=ROOT)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = sys.argv[1], sys.argv[2:]
    host = os.getenv("STEPGUARD_API_HOST", "127.0.0.1")
    port = os.getenv("STEPGUARD_API_PORT", "8000")
    api = [PY, "-m", "uvicorn", "backend.main:app", "--host", host, "--port", port]
    ui = [PY, "-m", "streamlit", "run", str(ROOT / "frontend" / "app.py"), "--server.headless", "true"]
    if cmd == "init-db":
        return sh([PY, str(ROOT / "scripts" / "initialize_database.py"), *rest])
    if cmd == "api":
        return sh(api + rest)
    if cmd == "ui":
        return sh(ui + rest)
    if cmd == "all":
        p = subprocess.Popen(api, cwd=ROOT)
        try:
            return sh(ui)
        finally:
            p.terminate()
    if cmd == "test":
        return sh([PY, "-m", "pytest", *rest])
    if cmd == "experiment":
        return sh([PY, str(ROOT / "scripts" / "run_experiments.py"), *rest])
    if cmd == "report":
        return sh([PY, str(ROOT / "scripts" / "generate_report.py"), *rest])
    print(f"unknown command '{cmd}'\n{__doc__}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
