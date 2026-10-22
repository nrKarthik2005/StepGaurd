"""Measured metrics, paired statistics, exports and the generated report for one experiment."""
import sys  # make the project root importable however Streamlit is launched
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pandas as pd
import streamlit as st  # noqa: E402

from backend.experiments.report import METRIC_ROWS
from frontend.components import charts
from frontend.components.ui import SHORT, experiment_picker, pct, safe, setup

client = setup("Results", "📊")
st.title("Results")
exp = experiment_picker(client, "res_exp")
if not exp:
    st.stop()
s = safe(client.metrics_summary, exp["id"])
if not s:
    st.stop()
m = s["metrics"]
cfgs = list(m)
st.caption(f"`{s['experiment_id']}` · mode {s['mode']} · {s['n_episodes']} episodes · "
           f"**{s['analysis']}** analysis. All values below are computed from the stored episodes.")
if s["analysis"] != "confirmatory":
    st.info("Exploratory run: use it for development. Only a `full` run with frozen specifications is labelled "
            "confirmatory.")

tab1, tab2, tab3, tab4 = st.tabs(["Metrics", "By fault & workflow", "Paired statistics", "Export & report"])
with tab1:
    rows = []
    for key, label, is_pct in METRIC_ROWS:
        row = {"metric": label}
        for c in cfgs:
            v = m[c].get(key)
            row[SHORT.get(c, c)] = pct(v) if is_pct else ("n/a" if v is None else
                                                         (f"{v:.3f}" if isinstance(v, float) else str(v)))
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("n/a = the denominator is zero or the metric does not apply (e.g. no verifier in A).")
    a, b = st.columns(2)
    a.plotly_chart(charts.metric_by_config(m, "TCR_fault", "Task completion under faults",
                                           ci_key="TCR_fault_ci"), width="stretch")
    b.plotly_chart(charts.metric_by_config(m, "PER", "Procedural error rate"), width="stretch")
    a.plotly_chart(charts.metric_by_config(m, "UAPR", "Unsafe action prevention rate"), width="stretch")
    b.plotly_chart(charts.metric_by_config(m, "EDR", "Error detection rate"), width="stretch")
with tab2:
    st.plotly_chart(charts.heatmap(s["per_condition"], "condition", cfgs, "Task completion by condition"),
                    width="stretch")
    main = [c for c in ("A_UNGUARDED", "B_INTERCEPTOR", "SG_FULL") if c in cfgs]
    st.plotly_chart(charts.grouped_by_condition(s["per_condition"], main,
                                                "Completion by condition: A, B and StepGuard"),
                    width="stretch")
    st.plotly_chart(charts.heatmap(s["per_family"], "family", cfgs, "Completion under faults by workflow"),
                    width="stretch")
with tab3:
    if s["comparisons"]:
        st.plotly_chart(charts.paired_diff(s["comparisons"], "StepGuard minus each other configuration"),
                        width="stretch")
        st.dataframe(pd.DataFrame([{"comparison": k.replace("_vs_", " vs "), **v} for k, v in
                                   s["comparisons"].items()]), hide_index=True, width="stretch")
        st.caption("Paired over identical scenarios. CI: cluster bootstrap over task templates. p: exact McNemar, "
                   "Holm-adjusted across comparisons. Effect size: Cohen's h.")
    st.markdown("**Hypotheses checked against this run**")
    st.dataframe(pd.DataFrame(s.get("hypotheses", [])), hide_index=True, width="stretch")
with tab4:
    c1, c2, c3 = st.columns(3)
    for col, kind, label, mime in ((c1, "csv", "Episodes (CSV)", "text/csv"),
                                   (c2, "json", "Summary (JSON)", "application/json"),
                                   (c3, "jsonl", "Full traces (JSONL)", "application/jsonl")):
        data = client.artifact_bytes(exp["id"], kind)
        if data:
            col.download_button(label, data, file_name=f"{exp['id']}-{kind}.{kind}", mime=mime)
        else:
            col.caption(f"{label}: not available")
    if st.button("Generate Markdown report", type="primary"):
        r = safe(client.report, exp["id"])
        if r:
            st.success(f"Written to {r['report']}")
            st.download_button("Download report", r["markdown"], file_name=f"{exp['id']}.md", mime="text/markdown")
            st.markdown(r["markdown"])
