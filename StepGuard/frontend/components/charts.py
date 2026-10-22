"""Plotly figure builders. Every figure is drawn from measured data passed in by the caller."""
from __future__ import annotations

from typing import Dict, List, Optional

import plotly.graph_objects as go

from frontend.components.ui import CONFIG_COLORS, SHORT, VERDICT_STYLE

INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
SEQ = [[0, "#cde2fb"], [0.25, "#86b6ef"], [0.5, "#3987e5"], [0.75, "#1c5cab"], [1, "#0d366b"]]


def _layout(fig: go.Figure, title: str = "", height: int = 360, yfmt: Optional[str] = None,
            legend: bool = True) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color=INK), x=0, xanchor="left"),
        height=height, margin=dict(l=8, r=8, t=48 if title else 16, b=8),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=dict(family="system-ui, Segoe UI, sans-serif",
                                                               color=INK2, size=12),
        showlegend=legend, legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, title=None),
        hoverlabel=dict(bgcolor="white", font_color=INK), bargap=0.28, bargroupgap=0.08,
    )
    fig.update_xaxes(showgrid=False, linecolor=AXIS, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=AXIS, tickfont=dict(color=MUTED),
                     tickformat=yfmt)
    return fig


def metric_by_config(metrics: Dict[str, Dict], key: str, title: str, pct: bool = True,
                     ci_key: Optional[str] = None) -> go.Figure:
    cfgs = [c for c in metrics if metrics[c].get(key) is not None]
    vals = [metrics[c][key] for c in cfgs]
    err = None
    if ci_key:
        lo = [v - (metrics[c].get(ci_key) or [v, v])[0] for c, v in zip(cfgs, vals)]
        hi = [(metrics[c].get(ci_key) or [v, v])[1] - v for c, v in zip(cfgs, vals)]
        err = dict(type="data", array=hi, arrayminus=lo, color=INK2, thickness=1.2, width=4)
    fig = go.Figure(go.Bar(
        x=[SHORT.get(c, c) for c in cfgs], y=vals, error_y=err,
        marker=dict(color=[CONFIG_COLORS.get(c, "#2a78d6") for c in cfgs], line=dict(color=SURFACE, width=2),
                    cornerradius=4),
        text=[f"{v*100:.1f}%" if pct else f"{v:.2f}" for v in vals], textposition="outside",
        textfont=dict(color=INK2), cliponaxis=False,
        hovertemplate="%{x}<br>" + title + ": %{text}<extra></extra>"))
    return _layout(fig, title, yfmt=".0%" if pct else None, legend=False)


def grouped_by_condition(per_condition: List[Dict], configs: List[str], title: str) -> go.Figure:
    conds = [r["condition"] for r in per_condition]
    fig = go.Figure()
    for c in configs:
        fig.add_bar(name=SHORT.get(c, c), x=conds, y=[r.get(c) for r in per_condition],
                    marker=dict(color=CONFIG_COLORS.get(c), line=dict(color=SURFACE, width=2), cornerradius=4),
                    hovertemplate=SHORT.get(c, c) + " · %{x}: %{y:.1%}<extra></extra>")
    fig.update_layout(barmode="group")
    return _layout(fig, title, yfmt=".0%")


def heatmap(per_rows: List[Dict], row_key: str, configs: List[str], title: str) -> go.Figure:
    rows = [r[row_key] for r in per_rows]
    z = [[r.get(c) for c in configs] for r in per_rows]
    text = [["n/a" if v is None else f"{v*100:.0f}%" for v in row] for row in z]
    fig = go.Figure(go.Heatmap(
        z=z, x=[SHORT.get(c, c) for c in configs], y=rows, zmin=0, zmax=1, colorscale=SEQ, text=text,
        texttemplate="%{text}", xgap=2, ygap=2, colorbar=dict(tickformat=".0%", thickness=10, outlinewidth=0),
        hovertemplate="%{y} · %{x}: %{text}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    return _layout(fig, title, height=max(260, 46 * len(rows) + 90), legend=False)


def verdict_counts(steps: List[Dict], title: str = "Verdicts in this run") -> go.Figure:
    order = ["ACCEPT", "BLOCK", "VIOLATE", "INCONCLUSIVE", "UNCHECKED"]
    counts = {v: sum(1 for s in steps if s["verdict"] == v) for v in order}
    keep = [v for v in order if counts[v]]
    fig = go.Figure(go.Bar(
        x=[counts[v] for v in keep], y=[f"{VERDICT_STYLE[v][1]} {v}" for v in keep], orientation="h",
        marker=dict(color=[VERDICT_STYLE[v][0] for v in keep], cornerradius=4), text=[counts[v] for v in keep],
        textposition="outside", cliponaxis=False, hovertemplate="%{y}: %{x}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    return _layout(fig, title, height=60 + 44 * max(1, len(keep)), legend=False)


def timeline(steps: List[Dict], title: str = "Step timeline (virtual time)") -> go.Figure:
    fig = go.Figure()
    for v, (color, icon) in VERDICT_STYLE.items():
        pts = [s for s in steps if s["verdict"] == v]
        if not pts:
            continue
        fig.add_scatter(
            x=[s.get("virtual_time", 0) for s in pts], y=[s["action"] for s in pts], mode="markers",
            name=f"{icon} {v}", marker=dict(size=12, color=color, line=dict(color=SURFACE, width=2),
                                            symbol="circle"),
            customdata=[[s["step"], s.get("recovery"), s.get("failed_predicate") or ""] for s in pts],
            hovertemplate="step %{customdata[0]} · %{y}<br>recovery: %{customdata[1]}<br>"
                          "%{customdata[2]}<extra>" + v + "</extra>")
    fig.update_xaxes(title=dict(text="virtual seconds", font=dict(color=MUTED)))
    return _layout(fig, title, height=320)


def strategy_bars(counts: Dict[str, int], title: str) -> go.Figure:
    items = sorted(counts.items(), key=lambda kv: -kv[1])
    fig = go.Figure(go.Bar(x=[k for k, _ in items], y=[v for _, v in items], text=[v for _, v in items],
                           textposition="outside", cliponaxis=False,
                           marker=dict(color="#2a78d6", cornerradius=4),
                           hovertemplate="%{x}: %{y}<extra></extra>"))
    return _layout(fig, title, legend=False)


def paired_diff(comparisons: Dict[str, Dict], title: str) -> go.Figure:
    names, d, lo, hi = [], [], [], []
    for k, v in comparisons.items():
        other = k.split("_vs_")[-1]
        names.append(f"StepGuard vs {SHORT.get(other, other)}")
        d.append(v["difference"])
        lo.append(v["difference"] - (v["ci_low"] if v["ci_low"] is not None else v["difference"]))
        hi.append((v["ci_high"] if v["ci_high"] is not None else v["difference"]) - v["difference"])
    fig = go.Figure(go.Scatter(
        x=d, y=names, mode="markers", marker=dict(size=11, color="#1baf7a", line=dict(color=SURFACE, width=2)),
        error_x=dict(type="data", array=hi, arrayminus=lo, color=INK2, thickness=1.5, width=5),
        hovertemplate="%{y}: %{x:+.1%}<extra></extra>"))
    fig.add_vline(x=0, line_color=AXIS, line_width=1)
    fig.update_xaxes(tickformat="+.0%", title=dict(text="difference in completion under faults (95% CI)",
                                                   font=dict(color=MUTED)), showgrid=True, gridcolor=GRID)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    return _layout(fig, title, height=90 + 46 * len(names), legend=False)
