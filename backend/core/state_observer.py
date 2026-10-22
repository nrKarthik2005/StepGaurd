"""Read-only state observation.

The observer is StepGuard's only view of the environment. It never trusts a tool's own
success message. Two kinds of read are offered:

  observe()              an ordinary read. With probability `read_lag` immediately after a
                         write it returns the state as it was before that write, modelling
                         an eventually consistent read replica. This is what produces
                         verifier false positives in the experiments.
  observe_consistent()   a strongly consistent read (e.g. a read-by-key on the primary),
                         used by effect probes and restoration checks.
"""
from __future__ import annotations

import random
from typing import Any, Dict, Optional

from backend.environments.base import Environment


class StateObserver:
    def __init__(self, env: Environment, rng: Optional[random.Random] = None, read_lag: float = 0.0):
        self.env = env
        self.rng = rng or random.Random(0)
        self.read_lag = read_lag
        self.reads = 0
        self._stale: Optional[Dict[str, Any]] = None

    def before_write(self, obs_before: Dict[str, Any]) -> None:
        """Called by the executor just before a tool runs; arms the stale-read model."""
        self._stale = obs_before

    def observe(self) -> Optional[Dict[str, Any]]:
        self.reads += 1
        stale, self._stale = self._stale, None
        if stale is not None and self.read_lag > 0 and self.rng.random() < self.read_lag:
            return dict(stale)
        return self._safe_read()

    def observe_consistent(self) -> Optional[Dict[str, Any]]:
        self.reads += 1
        self._stale = None
        return self._safe_read()

    def _safe_read(self) -> Optional[Dict[str, Any]]:
        try:
            return self.env.observe()
        except Exception:
            return None          # unobservable -> verification becomes INCONCLUSIVE
