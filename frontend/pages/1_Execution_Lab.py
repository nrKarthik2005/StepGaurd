"""Run one episode and inspect every verification decision."""
import json

import sys  # make the project root importable however Streamlit is launched
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st  # noqa: E402

from frontend.components import charts
from frontend.components.ui import CONDITION_LABEL, SHORT, badge, safe, setup, steps_table

client = setup("Execution Lab", "🧪")
st.title("Execution Lab")
st.markdown("Run a single seeded episode of a workflow under one configuration and fault. "
            "Each step shows StepGuard's verdict, the predicate that failed, the state probe and the recovery chosen.")

wfs = safe(client.workflows) or []
if not wfs:
    st.stop()
with st.form("sim"):
    c1, c2, c3 = st.columns(3)
    wf = c1.selectbox("Workflow", [w["name"] for w in wfs])
    conf = c2.selectbox("Configuration", list(SHORT), index=2, format_func=lambda c: f"{SHORT[c]} ({c})")
    fault = c3.selectbox("Injected fault", list(CONDITION_LABEL), format_func=CONDITION_LABEL.get, index=4)
    c4, c5, c6, c7 = st.columns(4)
    template = c4.number_input("Task template", 0, 999, 0)
    seed = c5.number_input("Parameter seed", 0, 999, 0)
    lag = c6.slider("Read-after-write lag probability", 0.0, 0.5, 0.0, 0.01,
                    help="Chance that an ordinary state read returns the pre-write state (stale read).")
    health = safe(client.health) or {}
    agents = ["rule_based"] + (["llm"] if health.get("llm_adapter_configured") else [])
    agent = c7.selectbox("Agent", agents, help="The LLM option appears only when STEPGUARD_LLM_* is set.")
    compare = st.checkbox("Also run the unguarded agent (A) on the same scenario for comparison", value=True)
    go = st.form_submit_button("Run episode", type="primary")

if go:
    body = dict(workflow=wf, configuration=conf, fault=None if fault == "clean" else fault, template=int(template),
                seed=int(seed), read_lag=float(lag), agent=agent)
    with st.spinner("Running…"):
        st.session_state["lab_result"] = safe(client.simulate, body)
        st.session_state["lab_ref"] = safe(client.simulate, {**body, "configuration": "A_UNGUARDED"}) \
            if compare and conf != "A_UNGUARDED" else None

res = st.session_state.get("lab_result")
if not res:
    st.info("Choose a scenario and press **Run episode**.")
    st.stop()


def outcome(r):
    if r["completed"]:
        return "✔ completed"
    return "⚠ escalated" if r["escalated"] else "✖ not completed"


st.subheader(f"Result · {SHORT.get(res['configuration'], res['configuration'])} · "
             f"{CONDITION_LABEL.get(res['fault'] or 'clean')}")
k = st.columns(6)
k[0].metric("Outcome", outcome(res))
k[1].metric("Final stage", res.get("final_stage") or "not tracked",
            help="Unguarded and interceptor runs have no workflow model, so they do not track a stage.")
k[2].metric("Fault fired", ("yes" if res["fault_fired"] else "no")
            + (f" ({res['fault_variant']})" if res.get("fault_variant") else ""))
k[3].metric("Unsafe actions executed", res["unsafe_executed"])
k[4].metric("Duplicate side effects", res["duplicate_side_effects"])
k[5].metric("Tool executions", res["executed"])
if res.get("escalation_reason"):
    st.warning(f"Escalation reason: {res['escalation_reason']}")
ref = st.session_state.get("lab_ref")
if ref:
    st.markdown(f"<div class='sg-card'>Same scenario, unguarded agent (A): <b>{outcome(ref)}</b> · "
                f"final stage <code>{ref.get('final_stage') or 'not tracked'}</code> · unsafe actions executed "
                f"<b>{ref['unsafe_executed']}</b> · duplicate side effects <b>{ref['duplicate_side_effects']}</b>"
                f"</div>", unsafe_allow_html=True)

a, b = st.columns([1, 2])
a.plotly_chart(charts.verdict_counts(res["steps"]), width="stretch")
b.plotly_chart(charts.timeline(res["steps"]), width="stretch")
st.markdown("**Step log**")
steps_table(res["steps"])

st.markdown("**Step detail**")
for s in res["steps"]:
    with st.expander(f"Step {s['step']} · {s['action']} · {s['verdict']}"
                     + (f" · recovery {s['recovery']}" if s.get("recovery") not in (None, "NONE") else "")):
        st.markdown(badge(s["verdict"]) + (f" &nbsp; failed predicate <code>{s['failed_predicate']}</code>"
                                           if s.get("failed_predicate") else ""), unsafe_allow_html=True)
        if s.get("message"):
            st.write(s["message"])
        c1, c2 = st.columns(2)
        c1.markdown("Arguments")
        c1.json(s.get("args") or {})
        c2.markdown("Output" if s.get("output") is not None else "Error")
        c2.json(s.get("output") if s.get("output") is not None else {"error": s.get("error")})
        st.caption(f"stage before `{s.get('stage_before')}` · expected `{s.get('expected_stage')}` · observed "
                   f"`{s.get('observed_stage')}` · probe {s.get('probe')} · admissible {s.get('admissible')} · "
                   f"checkpoint {s.get('checkpoint')} · barrier {s.get('barrier')} · in-flight {s.get('in_flight')} · "
                   f"restoration verified {s.get('restoration_verified')} · oracle unsafe {s.get('oracle_unsafe')}")
        if s.get("recovery_reason"):
            st.caption(f"Recovery reason: {s['recovery_reason']}")

with st.expander("Checkpoint events"):
    st.dataframe(res.get("checkpoints") or [], width="stretch")
with st.expander("Task parameters and raw JSON"):
    st.json(res.get("task", {}))
    st.download_button("Download episode JSON", json.dumps(res, indent=2, default=str),
                       file_name=f"{res.get('simulation_id', 'episode')}.json", mime="application/json")
