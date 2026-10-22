"""StepGuard dashboard — overview page.  Run:  streamlit run frontend/app.py"""
import sys  # make the project root importable however Streamlit is launched
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st  # noqa: E402

from frontend.components import charts
from frontend.components.ui import SHORT, pct, safe, setup

client = setup("Overview")
st.title("StepGuard")
st.markdown("Runtime verification and state-aware recovery for procedural errors in tool-using agents. "
            "This dashboard shows **only results produced by runs of the local prototype**; "
            "where nothing has been run yet, it says so.")

health = safe(client.health)
wfs = safe(client.workflows) or []
c1, c2, c3, c4 = st.columns(4)
c1.metric("Backend", "online" if client.remote else "in-process")
c2.metric("Workflows loaded", len(wfs))
c3.metric("Experiments stored", len(safe(client.experiments) or []))
c4.metric("LLM adapter", "configured" if health and health.get("llm_adapter_configured") else "not configured",
          help="Optional. The deterministic rule-based agent is always available.")

st.subheader("Workflows")
for w in wfs:
    with st.expander(f"**{w['name']}** · {len(w['states'])} states · {len(w['tools'])} tools · "
                     f"sha256 {w['sha256'][:12]}"):
        st.write(w["description"])
        st.markdown("States: " + " → ".join(f"`{s}`" for s in w["states"]))
        st.dataframe([{"tool": t["name"], "reversibility": t["reversibility"], "idempotent": t["idempotent"],
                       "requires": ", ".join(t["requires"]), "preconditions": ", ".join(t["preconditions"]),
                       "effect checks": ", ".join(t["effect"]), "recovery": ", ".join(t["recovery"])}
                      for t in w["tools"]], hide_index=True, width="stretch")

st.subheader("Latest measured results")
try:
    summary = client.metrics_summary()
except Exception:
    summary = None
if not summary:
    st.info("No experiment has been completed yet. Go to **Experiments** to run one "
            "(the quick mode takes a few seconds).")
else:
    m = summary["metrics"]
    st.caption(f"Experiment `{summary['experiment_id']}` · mode {summary['mode']} · "
               f"{summary['n_episodes']} episodes · {summary['analysis']} analysis")
    cols = st.columns(len(m))
    for col, (cfg, v) in zip(cols, m.items()):
        col.metric(SHORT.get(cfg, cfg), pct(v.get("TCR_fault")), help="Task completion under injected faults")
    st.plotly_chart(charts.metric_by_config(m, "TCR_fault", "Task completion under faults (95% Wilson CI)",
                                            ci_key="TCR_fault_ci"), width="stretch")
    st.caption("Open **Results** for every metric, per-fault breakdowns, paired statistics and exports.")
