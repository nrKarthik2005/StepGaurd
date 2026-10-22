"""Start paired experiments and track their status."""
import time

import sys  # make the project root importable however Streamlit is launched
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st  # noqa: E402

from frontend.components.ui import CONDITION_LABEL, SHORT, safe, setup

client = setup("Experiments", "🧮")
st.title("Experiments")
st.markdown("Every scenario (workflow × task template × seed × condition) is run under **every selected "
            "configuration**, so comparisons are paired. The unguarded agent A is always included as the reference.")

modes = {"quick": "quick · 5 templates × 1 seed", "standard": "standard · 10 templates × 2 seeds",
         "full": "full · 25 templates × 3 seeds (confirmatory if specs stay frozen)"}
wfs = [w["name"] for w in (safe(client.workflows) or [])]
with st.form("exp"):
    mode = st.radio("Mode", list(modes), format_func=modes.get, horizontal=True)
    sel_wf = st.multiselect("Workflows", wfs, default=wfs)
    sel_cfg = st.multiselect("Configurations", list(SHORT), default=list(SHORT), format_func=SHORT.get)
    sel_cond = st.multiselect("Conditions", list(CONDITION_LABEL), default=list(CONDITION_LABEL),
                              format_func=CONDITION_LABEL.get)
    c1, c2 = st.columns(2)
    lag = c1.slider("Read-after-write lag probability", 0.0, 0.3, 0.03, 0.01)
    note = c2.text_input("Note (stored with the run)")
    submitted = st.form_submit_button("Run experiment", type="primary")

if submitted:
    if not sel_wf or not sel_cond:
        st.error("Select at least one workflow and one condition.")
    else:
        body = dict(mode=mode, workflows=sel_wf, configurations=sel_cfg or None, conditions=sel_cond,
                    read_lag=lag, note=note)
        with st.spinner("Running experiment… (larger runs continue in the background)"):
            r = safe(client.run_experiment, body)
        if r:
            st.success(f"Experiment `{r['experiment_id']}`: {r['status']} · {r['n_episodes']} episodes. {r['detail']}")

st.subheader("Stored experiments")
exps = safe(client.experiments) or []
if not exps:
    st.info("None yet.")
else:
    st.dataframe(exps, hide_index=True, width="stretch",
                 column_order=["id", "mode", "status", "analysis", "n_planned", "n_episodes", "created_at",
                               "finished_at", "error"])
    if any(e["status"] == "running" for e in exps):
        st.caption("A run is in progress; this list refreshes every 5 s.")
        time.sleep(5)
        st.rerun()
