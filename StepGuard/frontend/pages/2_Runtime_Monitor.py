"""Browse stored verification traces and the application log."""
import sys  # make the project root importable however Streamlit is launched
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pandas as pd
import streamlit as st  # noqa: E402

from frontend.components import charts
from frontend.components.ui import SHORT, experiment_picker, safe, setup, steps_table

client = setup("Runtime Monitor", "📡")
st.title("Runtime Monitor")
st.markdown("Every verification decision recorded during an experiment, filterable by configuration, verdict "
            "and episode.")

exp = experiment_picker(client, "mon_exp")
if exp:
    c1, c2, c3 = st.columns(3)
    conf = c1.selectbox("Configuration", ["(all)"] + list(SHORT), format_func=lambda c: SHORT.get(c, c))
    verdict = c2.selectbox("Verdict", ["(all)", "ACCEPT", "BLOCK", "VIOLATE", "INCONCLUSIVE", "UNCHECKED"])
    limit = c3.select_slider("Rows", [100, 250, 500, 1000, 2000, 5000], value=500)
    rows = safe(client.trace, exp["id"], None, None if conf == "(all)" else conf,
                None if verdict == "(all)" else verdict, limit) or []
    st.caption(f"{len(rows)} step events shown (limit {limit}).")
    if rows:
        a, b = st.columns([1, 2])
        a.plotly_chart(charts.verdict_counts(rows, "Verdicts in this selection"), width="stretch")
        df = pd.DataFrame(rows)
        by = df.groupby(["configuration", "verdict"]).size().unstack(fill_value=0)
        by.index = [SHORT.get(i, i) for i in by.index]
        b.markdown("**Verdicts by configuration (this selection)**")
        b.dataframe(by, width="stretch")
        episodes = sorted(df["episode_id"].unique())
        ep = st.selectbox("Inspect one episode", episodes, format_func=lambda e: e.split("/", 1)[-1])
        ep_rows = safe(client.trace, exp["id"], ep, None, None, 500) or []
        steps_table(ep_rows)
        st.download_button("Download this selection (CSV)", df.to_csv(index=False), "trace_selection.csv",
                           "text/csv")

st.subheader("Application log")
n = st.slider("Lines", 20, 1000, 150, 10)
st.code(safe(client.logs, n) or "(log is empty)", language="log")
