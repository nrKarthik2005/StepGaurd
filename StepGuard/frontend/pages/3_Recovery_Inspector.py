"""Recovery decisions, admissibility and checkpoint restoration for a stored experiment."""
import sys  # make the project root importable however Streamlit is launched
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pandas as pd
import streamlit as st  # noqa: E402

from frontend.components import charts
from frontend.components.ui import SHORT, experiment_picker, safe, setup

client = setup("Recovery Inspector", "🛠️")
st.title("Recovery Inspector")
st.markdown("Which recovery strategy StepGuard chose after each non-accepted step, which strategies were "
            "admissible at that moment, and whether checkpoint restorations were verified.")

exp = experiment_picker(client, "rec_exp")
if not exp:
    st.stop()
guarded = [c for c in SHORT if c.startswith("SG") or c == "B_INTERCEPTOR"]
conf = st.selectbox("Configuration", guarded, index=guarded.index("SG_FULL"), format_func=lambda c: SHORT[c])
data = safe(client.recovery, exp["id"], conf) or {"decisions": [], "checkpoints": [], "faults": [],
                                                  "checkpoint_counts": []}
dec = pd.DataFrame(data["decisions"])
ck = pd.DataFrame(data["checkpoints"])
fl = pd.DataFrame(data["faults"])

k = st.columns(4)
k[0].metric("Recovery decisions", len(dec))
k[1].metric("Faults that fired", len(fl))
restores = ck[ck["event"].isin(["RESTORE", "RESTORE_FAILED"])] if not ck.empty else ck
k[2].metric("Checkpoint restorations", len(restores))
k[3].metric("Restorations verified", "n/a" if restores.empty else f"{int(restores['verified'].sum())}/{len(restores)}")

if dec.empty:
    st.info("No recovery decisions were recorded for this configuration in this experiment.")
    st.stop()
a, b = st.columns(2)
a.plotly_chart(charts.strategy_bars(dec["strategy"].value_counts().to_dict(), "Strategies chosen"),
               width="stretch")
b.markdown("**Strategy by probe result**")
b.dataframe(pd.crosstab(dec["probe"], dec["strategy"]), width="stretch")
st.markdown("**Strategy by fault condition**")
st.dataframe(pd.crosstab(dec["condition"], dec["strategy"]), width="stretch")

st.markdown("**Decisions**")
f1, f2 = st.columns(2)
strat = f1.multiselect("Strategy", sorted(dec["strategy"].unique()))
wf = f2.multiselect("Workflow", sorted(dec["workflow"].unique()))
view = dec
if strat:
    view = view[view["strategy"].isin(strat)]
if wf:
    view = view[view["workflow"].isin(wf)]
view = view.assign(admissible=view["admissible"].map(lambda x: ", ".join(x)),
                   episode=view["episode_id"].str.split("/", n=1).str[-1])
st.dataframe(view[["episode", "step", "action", "verdict", "probe", "strategy", "admissible", "reason"]],
             hide_index=True, width="stretch")

counts = data.get("checkpoint_counts") or []
if counts:
    st.markdown("**Checkpoint events (all)**")
    st.dataframe(counts, hide_index=True)
if not ck.empty:
    st.markdown("**Restorations**")
    ck = ck.assign(episode=ck["episode_id"].str.split("/", n=1).str[-1])
    st.dataframe(ck[["episode", "step", "event", "idx", "stage", "barrier", "verified"]], hide_index=True,
                 width="stretch")
