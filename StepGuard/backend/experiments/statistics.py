"""Statistical helpers: exact McNemar, cluster bootstrap, Wilson intervals, effect sizes, Holm."""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import binomtest


def wilson(successes: int, n: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float]]:
    if n == 0:
        return None, None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def mcnemar_exact(only_first: int, only_second: int) -> float:
    """Exact two-sided McNemar test on discordant pairs."""
    n = only_first + only_second
    if n == 0:
        return 1.0
    return float(binomtest(only_first, n, 0.5).pvalue)


def cohens_h(p1: float, p2: float) -> float:
    return 2 * math.asin(math.sqrt(p1)) - 2 * math.asin(math.sqrt(p2))


def cluster_bootstrap_diff(diffs_by_cluster: Dict[str, List[float]], n_boot: int = 2000,
                           seed: int = 7) -> Tuple[float, Optional[float], Optional[float]]:
    """Mean paired difference with a percentile CI resampling whole clusters (task templates)."""
    clusters = sorted(diffs_by_cluster)
    allv = [v for c in clusters for v in diffs_by_cluster[c]]
    if not allv:
        return 0.0, None, None
    mean = float(np.mean(allv))
    if len(clusters) < 2:
        return mean, None, None
    rng = np.random.default_rng(seed)
    sums = np.array([sum(diffs_by_cluster[c]) for c in clusters])
    counts = np.array([len(diffs_by_cluster[c]) for c in clusters])
    idx = rng.integers(0, len(clusters), size=(n_boot, len(clusters)))
    boots = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return mean, float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def holm(pvalues: Dict[str, float]) -> Dict[str, float]:
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 0.0
    for i, (k, p) in enumerate(items):
        adj = min(1.0, p * (m - i))
        running = max(running, adj)
        out[k] = running
    return out


def paired_comparison(first: Sequence[dict], second: Sequence[dict], key, cluster, outcome: str = "completed",
                      n_boot: int = 2000) -> Dict:
    """Compare two configurations on the same scenarios (rows matched by `key`)."""
    b = {key(r): r for r in second}
    pairs = [(r, b[key(r)]) for r in first if key(r) in b]
    only_first = sum(1 for x, y in pairs if x[outcome] and not y[outcome])
    only_second = sum(1 for x, y in pairs if y[outcome] and not x[outcome])
    diffs: Dict[str, List[float]] = {}
    for x, y in pairs:
        diffs.setdefault(cluster(x), []).append(float(x[outcome]) - float(y[outcome]))
    mean, lo, hi = cluster_bootstrap_diff(diffs, n_boot)
    p1 = float(np.mean([x[outcome] for x, _ in pairs])) if pairs else 0.0
    p2 = float(np.mean([y[outcome] for _, y in pairs])) if pairs else 0.0
    return {"n_pairs": len(pairs), "rate_first": round(p1, 4), "rate_second": round(p2, 4),
            "difference": round(mean, 4), "ci_low": None if lo is None else round(lo, 4),
            "ci_high": None if hi is None else round(hi, 4), "only_first": only_first, "only_second": only_second,
            "mcnemar_p": mcnemar_exact(only_first, only_second), "cohens_h": round(cohens_h(p1, p2), 4)}
