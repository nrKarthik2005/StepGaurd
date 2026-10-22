"""Data-access layer for experiments, episodes, traces and simulations."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.core.audit_logger import redact
from backend.core.models import RunResult, Strategy
from backend.database.connection import connect, init_db

NUMERIC = ("TCR_clean", "TCR_fault", "PER", "EDR", "RSR", "UAPR", "ETO", "ETO_all", "dTC", "dReads", "FPRv",
           "FPRv_all", "escalation_rate", "duplicate_side_effects", "mean_retries", "recovery_latency_mean",
           "verification_ms_per_step", "virtual_time_mean")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _j(x) -> str:
    return json.dumps(redact(x), default=str)


class Repository:
    def __init__(self, path: Optional[Path] = None):
        self.path = init_db(path)

    def conn(self):
        return connect(self.path)

    # ------------------------------------------------------------------ workflows
    def save_workflows(self, workflows: Dict[str, Any]) -> None:
        with self.conn() as c:
            for wf in workflows.values():
                c.execute("INSERT OR IGNORE INTO workflows(name, version, sha256, family, yaml, loaded_at) "
                          "VALUES (?,?,?,?,?,?)", (wf.name, wf.spec.version, wf.sha256, wf.spec.family,
                                                    wf.source, _now()))

    def list_workflow_versions(self) -> List[Dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT name, version, sha256, family, loaded_at FROM workflows ORDER BY name, loaded_at")]

    # ------------------------------------------------------------------ experiments
    def create_experiment(self, exp_id: str, mode: str, cfg: Dict, spec_hashes: Dict, n_planned: int) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO experiments(id, mode, status, created_at, config_json, spec_hashes_json, n_planned) "
                      "VALUES (?,?,?,?,?,?,?)", (exp_id, mode, "running", _now(), _j(cfg), _j(spec_hashes), n_planned))

    def fail_experiment(self, exp_id: str, error: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE experiments SET status='failed', error=?, finished_at=? WHERE id=?", (error, _now(), exp_id))

    def finish_experiment(self, exp_id: str, summary: Dict) -> None:
        with self.conn() as c:
            c.execute("UPDATE experiments SET status='completed', finished_at=?, n_episodes=?, analysis=?, "
                      "summary_json=? WHERE id=?", (_now(), summary.get("n_episodes", 0), summary.get("analysis"),
                                                     _j(summary), exp_id))
            for cfg, m in summary.get("metrics", {}).items():
                for k in NUMERIC:
                    v = m.get(k)
                    c.execute("INSERT OR REPLACE INTO metric_summaries VALUES (?,?,?,?)",
                              (exp_id, cfg, k, None if v is None else float(v)))

    def save_results(self, exp_id: str, results, rows: List[Dict]) -> None:
        with self.conn() as c:
            scen_ids: Dict[tuple, int] = {}
            for _, sc, _ in results:
                k = (sc.workflow, sc.template, sc.seed, sc.condition)
                if k not in scen_ids:
                    cur = c.execute("INSERT OR IGNORE INTO scenarios(experiment_id, workflow, template, seed, condition) "
                                    "VALUES (?,?,?,?,?)", (exp_id, *k))
                    scen_ids[k] = cur.lastrowid or c.execute(
                        "SELECT id FROM scenarios WHERE experiment_id=? AND workflow=? AND template=? AND seed=? "
                        "AND condition=?", (exp_id, *k)).fetchone()[0]
            ep_rows, steps, faults, ckpts, recs = [], [], [], [], []
            for (ep_id, sc, res), row in zip(results, rows):
                gid = f"{exp_id}/{ep_id}"
                ep_rows.append((gid, exp_id, scen_ids[(sc.workflow, sc.template, sc.seed, sc.condition)],
                                res.configuration, int(res.completed), int(res.escalated), res.escalation_reason,
                                int(res.fault_fired), res.fault_variant, res.fault_step, int(res.detected),
                                res.executed, res.unsafe_executed, res.unsafe_proposed, res.unsafe_blocked,
                                res.correct_steps, res.false_flags, res.proposals, res.blocks, res.retries,
                                res.tool_calls, res.state_reads, res.duplicate_side_effects, res.virtual_time,
                                res.verification_ms, res.recovery_latency, res.final_stage))
                self._collect(gid, res, steps, faults, ckpts, recs)
            c.executemany("INSERT INTO episodes VALUES (" + ",".join("?" * 27) + ")", ep_rows)
            self._insert_children(c, steps, faults, ckpts, recs)

    def _collect(self, gid: str, res: RunResult, steps, faults, ckpts, recs) -> None:
        for s in res.steps:
            steps.append((gid, s.step, s.action, _j(s.args), s.origin, s.stage_before, s.expected_stage,
                          s.observed_stage, s.verdict.value, s.failed_predicate, s.message, _j(s.output), s.error,
                          s.fault, s.probe.value, s.recovery.value, s.recovery_reason, s.checkpoint, s.barrier,
                          _j(s.in_flight), None if s.restoration_verified is None else int(s.restoration_verified),
                          int(s.executed), None if s.oracle_unsafe is None else int(s.oracle_unsafe), s.timestamp,
                          s.verification_ms, s.virtual_time))
            if s.recovery != Strategy.NONE:
                recs.append((gid, s.step, s.action, s.verdict.value, s.probe.value, s.recovery.value,
                             s.recovery_reason, _j(s.admissible)))
        if res.fault and res.fault_fired:
            faults.append((gid, res.fault, res.fault_variant, res.fault_step))
        for e in res.checkpoints:
            ckpts.append((gid, e.step, e.event, e.index, e.stage, e.history_len, e.barrier, int(e.verified)))

    @staticmethod
    def _insert_children(c, steps, faults, ckpts, recs) -> None:
        c.executemany("INSERT INTO step_events(episode_id, step, action, args_json, origin, stage_before, "
                      "expected_stage, observed_stage, verdict, failed_predicate, message, output_json, error, fault, "
                      "probe, recovery, recovery_reason, checkpoint, barrier, in_flight_json, restoration_verified, "
                      "executed, oracle_unsafe, timestamp, verification_ms, virtual_time) VALUES (" +
                      ",".join("?" * 26) + ")", steps)
        c.executemany("INSERT INTO injected_faults(episode_id, fault_type, variant, step) VALUES (?,?,?,?)", faults)
        c.executemany("INSERT INTO checkpoint_events(episode_id, step, event, idx, stage, history_len, barrier, "
                      "verified) VALUES (?,?,?,?,?,?,?,?)", ckpts)
        c.executemany("INSERT INTO recovery_decisions(episode_id, step, action, verdict, probe, strategy, reason, "
                      "admissible_json) VALUES (?,?,?,?,?,?,?,?)", recs)

    def list_experiments(self, limit: int = 100) -> List[Dict]:
        with self.conn() as c:
            rows = c.execute("SELECT id, mode, status, created_at, finished_at, n_planned, n_episodes, analysis, error "
                             "FROM experiments ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_experiment(self, exp_id: str) -> Optional[Dict]:
        with self.conn() as c:
            r = c.execute("SELECT * FROM experiments WHERE id=?", (exp_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["config"] = json.loads(d.pop("config_json"))
        d["spec_hashes"] = json.loads(d.pop("spec_hashes_json"))
        d["summary"] = json.loads(d.pop("summary_json")) if d.get("summary_json") else None
        return d

    def latest_completed(self) -> Optional[Dict]:
        with self.conn() as c:
            r = c.execute("SELECT id FROM experiments WHERE status='completed' AND mode != 'single' "
                          "ORDER BY finished_at DESC LIMIT 1").fetchone()
        return self.get_experiment(r["id"]) if r else None

    def episodes(self, exp_id: str) -> List[Dict]:
        with self.conn() as c:
            rows = c.execute("SELECT e.*, s.workflow, s.template, s.seed, s.condition FROM episodes e "
                             "JOIN scenarios s ON s.id = e.scenario_id WHERE e.experiment_id=?", (exp_id,)).fetchall()
        return [dict(r) for r in rows]

    def trace(self, exp_id: str, episode: Optional[str] = None, configuration: Optional[str] = None,
              verdict: Optional[str] = None, limit: int = 500, offset: int = 0) -> List[Dict]:
        q = ("SELECT st.* , e.configuration FROM step_events st JOIN episodes e ON e.id = st.episode_id "
             "WHERE e.experiment_id = ?")
        params: List[Any] = [exp_id]
        if episode:
            q += " AND st.episode_id = ?"
            params.append(episode if episode.startswith(exp_id) else f"{exp_id}/{episode}")
        if configuration:
            q += " AND e.configuration = ?"
            params.append(configuration)
        if verdict:
            q += " AND st.verdict = ?"
            params.append(verdict)
        q += " ORDER BY st.id LIMIT ? OFFSET ?"
        params += [limit, offset]
        with self.conn() as c:
            rows = [dict(r) for r in c.execute(q, params).fetchall()]
        for r in rows:
            for k in ("args_json", "output_json", "in_flight_json"):
                if r.get(k):
                    r[k[:-5]] = json.loads(r.pop(k))
        return rows

    def recovery(self, exp_id: str, configuration: Optional[str] = None, limit: int = 50000) -> Dict[str, List[Dict]]:
        """Recovery decisions, checkpoint events and injected faults for one experiment."""
        cond, params = "", [exp_id]
        if configuration:
            cond, params = " AND e.configuration = ?", [exp_id, configuration]
        out = {}
        with self.conn() as c:
            for name, table in (("decisions", "recovery_decisions"), ("faults", "injected_faults")):
                rows = c.execute(f"SELECT t.*, e.configuration, s.workflow, s.condition FROM {table} t "
                                 "JOIN episodes e ON e.id = t.episode_id JOIN scenarios s ON s.id = e.scenario_id "
                                 f"WHERE e.experiment_id = ?{cond} ORDER BY t.id LIMIT ?",
                                 params + [limit]).fetchall()
                out[name] = [dict(r) for r in rows]
            # restorations are returned in full; the much more frequent commits only as counts
            out["checkpoints"] = [dict(r) for r in c.execute(
                "SELECT t.*, e.configuration, s.workflow, s.condition FROM checkpoint_events t "
                "JOIN episodes e ON e.id = t.episode_id JOIN scenarios s ON s.id = e.scenario_id "
                f"WHERE e.experiment_id = ?{cond} AND t.event IN ('RESTORE', 'RESTORE_FAILED') ORDER BY t.id",
                params).fetchall()]
            out["checkpoint_counts"] = [dict(r) for r in c.execute(
                "SELECT t.event, t.verified, COUNT(*) AS count FROM checkpoint_events t "
                "JOIN episodes e ON e.id = t.episode_id "
                f"WHERE e.experiment_id = ?{cond} GROUP BY t.event, t.verified", params).fetchall()]
        for r in out["decisions"]:
            r["admissible"] = json.loads(r.pop("admissible_json") or "[]")
        return out

    # ------------------------------------------------------------------ simulations
    def save_simulation(self, result: Dict, workflow_sha: str) -> str:
        sid = "sim-" + uuid.uuid4().hex[:10]
        sc = result["scenario"]
        with self.conn() as c:
            c.execute("INSERT INTO simulations VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (sid, _now(), sc["workflow"], workflow_sha, sc["configuration"], sc["fault"], sc["template"],
                       sc["seed"], int(result["completed"]), _j(result)))
        return sid

    def list_simulations(self, limit: int = 50) -> List[Dict]:
        with self.conn() as c:
            rows = c.execute("SELECT id, created_at, workflow, configuration, fault, template, seed, completed "
                             "FROM simulations ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_simulation(self, sim_id: str) -> Optional[Dict]:
        with self.conn() as c:
            r = c.execute("SELECT result_json FROM simulations WHERE id=?", (sim_id,)).fetchone()
        return json.loads(r["result_json"]) if r else None
