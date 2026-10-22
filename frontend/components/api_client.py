"""Data access for the dashboard.

The dashboard talks to the FastAPI backend (STEPGUARD_BACKEND_URL, default http://127.0.0.1:8000).
If the backend is not running it falls back to calling the same Python functions in-process, so the
dashboard still works with only `streamlit run`. Both paths return the same JSON-shaped data, read from
real runs; nothing here produces or stores placeholder numbers.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402


class ApiError(Exception):
    pass


class Client:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 600.0):
        self.base = (base_url or config.BACKEND_URL).rstrip("/")
        self.timeout = timeout
        self.remote = self._probe()

    # ------------------------------------------------------------------ transport
    def _probe(self) -> bool:
        try:
            return httpx.get(f"{self.base}/health", timeout=1.5).status_code == 200
        except Exception:
            return False

    @property
    def mode(self) -> str:
        return f"API at {self.base}" if self.remote else "in-process (API not running)"

    def _get(self, path: str, **params):
        r = httpx.get(f"{self.base}{path}", params={k: v for k, v in params.items() if v is not None},
                      timeout=self.timeout)
        if r.status_code >= 400:
            raise ApiError(r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith(
                "application/json") else r.text)
        return r.json() if "json" in r.headers.get("content-type", "") else r.text

    def _post(self, path: str, body: Dict):
        r = httpx.post(f"{self.base}{path}", json=body, timeout=self.timeout)
        if r.status_code >= 400:
            detail = r.json().get("detail", r.text)
            raise ApiError(detail if isinstance(detail, str) else str(detail))
        return r.json()

    @staticmethod
    def _local(fn, *a, **kw):
        from fastapi import HTTPException
        from pydantic import ValidationError
        try:
            return fn(*a, **kw)
        except HTTPException as e:
            raise ApiError(e.detail) from None
        except ValidationError as e:
            raise ApiError("; ".join(err["msg"] for err in e.errors())) from None
        except (KeyError, ValueError) as e:
            raise ApiError(str(e)) from None

    @staticmethod
    def _routes():
        from backend.database.connection import init_db
        init_db()
        from backend.api import routes
        return routes

    # ------------------------------------------------------------------ endpoints
    def health(self) -> Dict:
        return self._get("/health").copy() if self.remote else self._local(self._routes().health).model_dump()

    def workflows(self) -> List[Dict]:
        if self.remote:
            return self._get("/workflows")
        return [w.model_dump() for w in self._local(self._routes().workflows)]

    def workflow(self, name: str) -> Dict:
        return self._get(f"/workflows/{name}") if self.remote else self._local(self._routes().workflow_detail, name)

    def experiments(self, limit: int = 200) -> List[Dict]:
        return self._get("/experiments", limit=limit) if self.remote else self._local(
            self._routes().list_experiments, limit)

    def experiment(self, exp_id: str) -> Dict:
        return self._get(f"/experiments/{exp_id}") if self.remote else self._local(
            self._routes().get_experiment, exp_id)

    def episodes(self, exp_id: str) -> List[Dict]:
        return self._get(f"/experiments/{exp_id}/episodes") if self.remote else self._local(
            self._routes().get_episodes, exp_id)

    def trace(self, exp_id: str, episode: Optional[str] = None, configuration: Optional[str] = None,
              verdict: Optional[str] = None, limit: int = 500, offset: int = 0) -> List[Dict]:
        if self.remote:
            return self._get(f"/experiments/{exp_id}/trace", episode=episode, configuration=configuration,
                             verdict=verdict, limit=limit, offset=offset)
        return self._local(self._routes().get_trace, exp_id, episode, configuration, verdict, limit, offset)

    def recovery(self, exp_id: str, configuration: Optional[str] = None) -> Dict:
        return self._get(f"/experiments/{exp_id}/recovery", configuration=configuration) if self.remote else \
            self._local(self._routes().get_recovery, exp_id, configuration, 50000)

    def run_experiment(self, body: Dict) -> Dict:
        if self.remote:
            return self._post("/experiments/run", body)
        from backend.api.schemas import ExperimentRequest
        req = self._local(ExperimentRequest, **body)
        return self._local(self._routes().start_experiment, req).model_dump()

    def simulate(self, body: Dict) -> Dict:
        if self.remote:
            return self._post("/simulations/run", body)
        from backend.api.schemas import SimulationRequest
        req = self._local(SimulationRequest, **body)
        return self._local(self._routes().simulate, req)

    def simulations(self, limit: int = 50) -> List[Dict]:
        return self._get("/simulations", limit=limit) if self.remote else self._local(
            self._routes().list_simulations, limit)

    def metrics_summary(self, exp_id: Optional[str] = None) -> Dict:
        return self._get("/metrics/summary", experiment_id=exp_id) if self.remote else self._local(
            self._routes().metrics_summary, exp_id)

    def report(self, exp_id: str) -> Dict:
        if self.remote:
            return self._post(f"/experiments/{exp_id}/report", {})
        return self._local(self._routes().make_report, exp_id)

    def logs(self, lines: int = 200) -> str:
        return self._get("/logs", lines=lines) if self.remote else self._local(self._routes().logs, lines)

    def artifact_bytes(self, exp_id: str, kind: str) -> Optional[bytes]:
        """Raw export file (csv | json | jsonl) for download buttons."""
        if self.remote:
            r = httpx.get(f"{self.base}/experiments/{exp_id}/export", params={"kind": kind}, timeout=self.timeout)
            return r.content if r.status_code == 200 else None
        name = {"csv": "episodes.csv", "json": "summary.json", "jsonl": "traces.jsonl"}[kind]
        p = config.RESULTS_DIR / exp_id / name
        return p.read_bytes() if p.exists() else None
