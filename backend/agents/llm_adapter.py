"""Optional LLM agent (OpenAI-compatible chat-completions API, e.g. a local Ollama server).

The LLM only *proposes* structured actions; every proposal still goes through StepGuard.
Configuration comes from environment variables (see .env.example):

  STEPGUARD_LLM_BASE_URL   e.g. http://localhost:11434/v1
  STEPGUARD_LLM_MODEL      e.g. llama3.1:8b
  STEPGUARD_LLM_API_KEY    optional; never logged

If the adapter is not configured, `LLMAgent.available()` is False and callers should use the
deterministic RuleBasedAgent. Malformed model responses are counted and turned into a
"no proposal" after `max_bad_responses`, never into an executed action.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import httpx

from backend import config
from backend.agents.base_agent import BaseAgent
from backend.core.audit_logger import get_logger
from backend.core.models import Action
from backend.core.workflow_engine import Workflow

log = get_logger("llm")

SYSTEM = ("You control tools for a workflow. Reply with ONLY a JSON object: "
          '{"tool": "<tool name>", "args": {...}} to propose the next call, or {"done": true} when finished.')


def parse_action(text: str, known_tools: List[str]) -> Optional[Action]:
    """Extract a single JSON action from model text. Returns None for 'done'.
    Raises ValueError for anything malformed or unknown."""
    if not isinstance(text, str):
        raise ValueError("response is not text")
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("no JSON object in response")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from None
    if not isinstance(obj, dict):
        raise ValueError("JSON is not an object")
    if obj.get("done") is True:
        return None
    tool, args = obj.get("tool"), obj.get("args", {})
    if tool not in known_tools:
        raise ValueError(f"unknown tool {tool!r}")
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    return Action(tool=tool, args=args, origin="llm")


class LLMAgent(BaseAgent):
    name = "llm"

    def __init__(self, workflow: Workflow, task: Dict[str, Any], client: Optional[httpx.Client] = None,
                 max_bad_responses: int = 3, timeout: float = 60.0):
        self.wf, self.task = workflow, task
        self.client = client
        self.timeout = timeout
        self.max_bad = max_bad_responses
        self.bad_responses = 0
        self.history: List[Dict[str, str]] = []
        self.memory: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def available() -> bool:
        return bool(config.LLM_BASE_URL and config.LLM_MODEL)

    def _tool_catalog(self) -> str:
        lines = []
        for name, c in self.wf.spec.tools.items():
            if "agent" in c.roles:
                args = ", ".join(f"{k}: {v.type}" for k, v in c.args.items())
                lines.append(f"- {name}({args}): {c.description}")
        return "\n".join(lines)

    def _ask(self) -> str:
        if not self.available() and self.client is None:
            raise RuntimeError("LLM adapter is not configured")
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"Task parameters: {json.dumps(self.task)[:2000]}\n"
                                                f"Tools:\n{self._tool_catalog()}"}] + self.history[-12:]
        headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"} if config.LLM_API_KEY else {}
        client = self.client or httpx.Client(timeout=self.timeout)
        r = client.post(f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions", headers=headers,
                        json={"model": config.LLM_MODEL, "messages": messages, "temperature": 0})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def propose(self) -> Optional[Action]:
        known = list(self.wf.spec.tools)
        while self.bad_responses < self.max_bad:
            try:
                text = self._ask()
            except Exception as e:                 # network or HTTP error: stop proposing safely
                log.warning("LLM request failed: %s", type(e).__name__)
                return None
            try:
                action = parse_action(text, known)
                self.history.append({"role": "assistant", "content": text[:500]})
                return action
            except ValueError as e:
                self.bad_responses += 1
                self.history.append({"role": "user", "content": f"Your last reply was invalid ({e}). Reply with JSON only."})
        log.warning("LLM produced %d malformed responses; stopping", self.bad_responses)
        return None

    def render(self, tool: str) -> Action:
        return Action(tool=tool, args={})

    def on_output(self, action: Action, output: Dict[str, Any]) -> None:
        self.memory[action.tool] = dict(output or {})
        self.history.append({"role": "user", "content": f"Result of {action.tool}: {json.dumps(output)[:800]}"})

    def on_error(self, action: Action, message: str, hint: Optional[str]) -> None:
        self.history.append({"role": "user", "content": f"{action.tool} failed: {message}"})

    def on_feedback(self, action: Action, feedback: Dict[str, Any]) -> None:
        self.history.append({"role": "user", "content": "StepGuard blocked your action: "
                             f"{feedback.get('reason')}. Allowed next actions: {feedback.get('enabled')}. "
                             f"Suggested fix: {feedback.get('fix')}"})
