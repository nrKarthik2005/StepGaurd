"""Shared page setup, colours and small display helpers."""
from __future__ import annotations

from typing import Dict, List, Optional

import streamlit as st

from frontend.components.api_client import ApiError, Client

# Reference categorical slots (validated order). Configurations are coloured by entity, never by rank.
CONFIG_COLORS = {
    "A_UNGUARDED": "#2a78d6", "B_INTERCEPTOR": "#eb6834", "SG_FULL": "#1baf7a",
    "SG_NO_PRE": "#6250d6", "SG_NO_POST": "#e87ba4", "SG_NO_CHECKPOINT": "#eda100",
    "SG_UNSELECTIVE_RECOVERY": "#e34948",
}
SHORT = {"A_UNGUARDED": "A · unguarded", "B_INTERCEPTOR": "B · interceptor", "SG_FULL": "StepGuard",
         "SG_NO_PRE": "SG − pre-checks", "SG_NO_POST": "SG − post-checks", "SG_NO_CHECKPOINT": "SG − checkpoints",
         "SG_UNSELECTIVE_RECOVERY": "SG − selective recovery"}
# Status palette (reserved): verdicts always appear with their label, never colour alone.
VERDICT_STYLE = {
    "ACCEPT": ("#0ca30c", "✔"), "BLOCK": ("#fab219", "⛔"), "VIOLATE": ("#d03b3b", "✖"),
    "INCONCLUSIVE": ("#ec835a", "?"), "UNCHECKED": ("#898781", "·"),
}
CONDITION_LABEL = {"clean": "clean", "E1": "E1 · out of order", "E2": "E2 · missing prerequisite",
                   "E3": "E3 · malformed output", "E4": "E4 · silent failure", "E5": "E5 · duplicate",
                   "E6": "E6 · timeout"}

CSS = """
<style>
  .block-container {padding-top: 2rem; max-width: 1280px;}
  .sg-badge {display:inline-block; padding:1px 8px; border-radius:10px; font-size:0.8rem; font-weight:600;
             border:1px solid rgba(11,11,11,0.10); background:#fcfcfb; color:#0b0b0b; white-space:nowrap;}
  .sg-dot {display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; vertical-align:middle;}
  .sg-muted {color:#52514e; font-size:0.88rem;}
  .sg-card {border:1px solid rgba(11,11,11,0.10); border-radius:8px; padding:12px 16px; background:#fcfcfb;}
  div[data-testid="stMetricValue"] {font-size: 1.6rem;}
</style>
"""


def setup(title: str, icon: str = "🛡️") -> Client:
    st.set_page_config(page_title=f"StepGuard · {title}", page_icon=icon, layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    client = get_client()
    with st.sidebar:
        st.caption("Data source")
        st.write(client.mode)
        if st.button("Reconnect", help="Re-check whether the FastAPI backend is running"):
            get_client.clear()
            st.rerun()
    return client


@st.cache_resource(show_spinner=False)
def get_client() -> Client:
    return Client()


def badge(verdict: str) -> str:
    color, icon = VERDICT_STYLE.get(verdict, ("#898781", "·"))
    return f'<span class="sg-badge"><span class="sg-dot" style="background:{color}"></span>{icon} {verdict}</span>'


def pct(v, digits: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:.{digits}f}%"


def safe(fn, *a, **kw):
    """Call the client and show a readable error instead of a traceback."""
    try:
        return fn(*a, **kw)
    except ApiError as e:
        st.error(str(e))
    except Exception as e:  # network errors etc.
        st.error(f"{type(e).__name__}: {e}")
    return None


def experiment_picker(client: Client, key: str, only_completed: bool = True) -> Optional[Dict]:
    exps = safe(client.experiments) or []
    if only_completed:
        exps = [e for e in exps if e.get("status") == "completed"]
    if not exps:
        st.info("No completed experiments yet. Start one on the **Experiments** page or run "
                "`python scripts/run_experiments.py --mode quick`.")
        return None
    labels = {e["id"]: f'{e["id"]} · {e["mode"]} · {e.get("n_episodes") or e.get("n_planned")} episodes'
              for e in exps}
    chosen = st.selectbox("Experiment", list(labels), format_func=labels.get, key=key)
    return next(e for e in exps if e["id"] == chosen)


def steps_table(steps: List[Dict]):
    import pandas as pd
    rows = [{"#": s["step"], "action": s["action"], "origin": s.get("origin"), "verdict": s["verdict"],
             "stage before": s.get("stage_before"), "expected": s.get("expected_stage"),
             "observed": s.get("observed_stage"), "failed predicate": s.get("failed_predicate"),
             "probe": s.get("probe"), "recovery": s.get("recovery"), "fault": s.get("fault"),
             "executed": s.get("executed"), "oracle: unsafe": s.get("oracle_unsafe")} for s in steps]
    df = pd.DataFrame(rows)

    def colour(v):
        c = VERDICT_STYLE.get(v, (None,))[0]
        return f"color: {c}; font-weight: 600" if c else ""
    if df.empty:
        st.write("No steps recorded.")
        return
    st.dataframe(df.style.map(colour, subset=["verdict"]), hide_index=True, width="stretch")
