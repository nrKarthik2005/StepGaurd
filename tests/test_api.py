"""REST API: status codes, validation and structured errors."""
import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok" and j["workflows"] == 4 and j["llm_adapter_configured"] is False


def test_docs_enabled(client):
    assert client.get("/docs").status_code == 200
    assert "/experiments/run" in client.get("/openapi.json").json()["paths"]


def test_workflows(client):
    r = client.get("/workflows")
    assert r.status_code == 200 and len(r.json()) == 4
    assert client.get("/workflows/order_processing").json()["spec"]["name"] == "order_processing"
    assert client.get("/workflows/unknown").status_code == 404


def test_simulation_and_validation(client):
    r = client.post("/simulations/run", json={"workflow": "order_processing", "fault": "E2"})
    assert r.status_code == 200
    j = r.json()
    assert j["completed"] is True and j["simulation_id"].startswith("sim-")
    assert client.get(f"/simulations/{j['simulation_id']}").status_code == 200
    assert client.post("/simulations/run", json={"workflow": "nope"}).status_code == 404
    assert client.post("/simulations/run", json={"workflow": "order_processing", "configuration": "X"}).status_code == 422
    assert client.post("/simulations/run", json={"workflow": "order_processing", "fault": "E9"}).status_code == 422
    assert client.post("/simulations/run", json={"workflow": "order_processing", "read_lag": 2}).status_code == 422
    r = client.post("/simulations/run", json={"workflow": "order_processing", "agent": "llm"})
    assert r.status_code == 422 and "not set" in r.json()["detail"]


def test_experiment_lifecycle(client):
    assert client.get("/metrics/summary").status_code in (200, 404)
    r = client.post("/experiments/run", json={"mode": "quick", "workflows": ["file_management"], "n_templates": 1,
                                              "configurations": ["SG_FULL"], "conditions": ["clean", "E1", "E4"]})
    assert r.status_code == 202
    j = r.json()
    assert j["status"] == "completed" and j["n_episodes"] == 6           # A is added for pairing
    eid = j["experiment_id"]
    e = client.get(f"/experiments/{eid}").json()
    assert e["status"] == "completed" and e["summary"]["n_episodes"] == 6
    assert any(x["id"] == eid for x in client.get("/experiments").json())
    trace = client.get(f"/experiments/{eid}/trace", params={"configuration": "SG_FULL", "limit": 1000}).json()
    assert trace and all(t["configuration"] == "SG_FULL" for t in trace)
    blocked = client.get(f"/experiments/{eid}/trace", params={"verdict": "BLOCK"}).json()
    assert all(t["verdict"] == "BLOCK" for t in blocked)
    assert client.get(f"/experiments/{eid}/trace", params={"verdict": "MAYBE"}).status_code == 422
    assert client.get(f"/experiments/{eid}/recovery").status_code == 200
    m = client.get("/metrics/summary", params={"experiment_id": eid}).json()
    assert set(m["metrics"]) == {"A_UNGUARDED", "SG_FULL"}
    for kind in ("csv", "json", "jsonl"):
        assert client.get(f"/experiments/{eid}/export", params={"kind": kind}).status_code == 200
    rep = client.post(f"/experiments/{eid}/report").json()
    assert eid in rep["markdown"]


def test_experiment_validation_errors(client):
    assert client.post("/experiments/run", json={"mode": "huge"}).status_code == 422
    assert client.post("/experiments/run", json={"configurations": ["Z"]}).status_code == 422
    assert client.post("/experiments/run", json={"workflows": ["nope"]}).status_code == 422
    assert client.post("/experiments/run", json={"conditions": []}).status_code == 422
    assert client.get("/experiments/does-not-exist").status_code == 404
    assert client.get("/experiments/does-not-exist/trace").status_code == 404


def test_logs(client):
    r = client.get("/logs", params={"lines": 20})
    assert r.status_code == 200 and isinstance(r.text, str)
