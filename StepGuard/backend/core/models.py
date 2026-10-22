"""Data models shared by every StepGuard component.

Specification models (workflows, contracts) are Pydantic models so that YAML files are
validated on load. Runtime records (step records, run results) are also Pydantic models so
they can be serialised straight into the API, SQLite and JSONL traces.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# --------------------------------------------------------------------------- enums
class Verdict(str, Enum):
    ACCEPT = "ACCEPT"
    BLOCK = "BLOCK"
    VIOLATE = "VIOLATE"
    INCONCLUSIVE = "INCONCLUSIVE"
    UNCHECKED = "UNCHECKED"          # baselines / ablations that do not verify a step


class Reversibility(str, Enum):
    R = "R"   # reversible by snapshot restoration
    C = "C"   # compensable by a registered compensating action
    I = "I"   # irreversible


class ProbeResult(str, Enum):
    APPLIED = "APPLIED"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"
    NOT_RUN = "NOT_RUN"


class Strategy(str, Enum):
    NONE = "NONE"
    RETRY = "RETRY"
    RESUME = "RESUME"
    ROLLBACK = "ROLLBACK"
    REPAIR = "REPAIR"
    ESCALATE = "ESCALATE"


class FaultType(str, Enum):
    E1 = "E1"  # ordering error
    E2 = "E2"  # missing prerequisite
    E3 = "E3"  # malformed output
    E4 = "E4"  # inconsistent transition (false success)
    E5 = "E5"  # duplicate action
    E6 = "E6"  # interruption (timeout before/after effect)


FAULT_DESCRIPTIONS = {
    "E1": "Ordering error: the proposed action is not valid in the current workflow state",
    "E2": "Missing prerequisite: a required resource, file or token is absent",
    "E3": "Malformed output: missing fields or wrong types in the tool response",
    "E4": "Inconsistent transition: the tool reports success but the state change is missing or wrong",
    "E5": "Duplicate action: a side-effecting operation is replayed (lost acknowledgement)",
    "E6": "Interruption: timeout before or after the effect is applied",
}


class Configuration(str, Enum):
    A = "A_UNGUARDED"
    B = "B_INTERCEPTOR"
    SG = "SG_FULL"
    SG_NO_PRE = "SG_NO_PRE"
    SG_NO_POST = "SG_NO_POST"
    SG_NO_CHECKPOINT = "SG_NO_CHECKPOINT"
    SG_UNSELECTIVE = "SG_UNSELECTIVE_RECOVERY"


CONFIG_DESCRIPTIONS = {
    "A_UNGUARDED": "Executes every proposal directly; no runtime verification",
    "B_INTERCEPTOR": "Argument-schema and permission checks; bounded retry with backoff on retryable exceptions",
    "SG_FULL": "Full StepGuard: pre/post verification, effect probes, verified checkpoints, admissible recovery",
    "SG_NO_PRE": "StepGuard without the precondition engine",
    "SG_NO_POST": "StepGuard without postcondition validation and effect probes",
    "SG_NO_CHECKPOINT": "StepGuard without verified checkpoints and rollback",
    "SG_UNSELECTIVE_RECOVERY": "StepGuard with uniform bounded retry instead of failure-aware selection (safety rules kept)",
}


# --------------------------------------------------------------------------- spec models
class ArgSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(pattern=r"^(str|int|float|bool|dict|list)$")
    required: bool = True
    min: Optional[float] = None
    max: Optional[float] = None
    pattern: Optional[str] = None
    source: Optional[str] = None   # where the rule-based agent takes the value from (task/memory)


class Condition(BaseModel):
    """A named, allowlisted predicate: either an expression or a registered predicate name."""
    model_config = ConfigDict(extra="forbid")
    name: str
    expr: Optional[str] = None
    predicate: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None
    fix: Optional[str] = None          # suggested repair action (tool name)

    @model_validator(mode="after")
    def one_of(self):
        if bool(self.expr) == bool(self.predicate):
            raise ValueError(f"condition '{self.name}' needs exactly one of 'expr' or 'predicate'")
        return self


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    roles: List[str] = Field(default_factory=lambda: ["agent"])
    requires: List[str] = Field(default_factory=list)
    args: Dict[str, ArgSpec] = Field(default_factory=dict)
    preconditions: List[Condition] = Field(default_factory=list)
    output_schema: Dict[str, str] = Field(default_factory=dict)
    effect: List[Condition] = Field(default_factory=list)
    unchanged: List[Condition] = Field(default_factory=list)    # holds when the effect is absent
    idempotent: bool = True
    reversibility: Reversibility = Reversibility.R
    key_fields: List[str] = Field(default_factory=list)
    compensation: Optional[str] = None
    compensation_args: Dict[str, str] = Field(default_factory=dict)
    resume_output: Dict[str, str] = Field(default_factory=dict)   # rebuild lost output fields on RESUME
    recovery: List[Strategy] = Field(default_factory=lambda: [Strategy.RETRY, Strategy.RESUME,
                                                              Strategy.ROLLBACK, Strategy.ESCALATE])

    @model_validator(mode="after")
    def check(self):
        if self.reversibility == Reversibility.C and not self.compensation:
            raise ValueError(f"tool '{self.name}' is compensable but has no compensation action")
        if not self.idempotent and not self.key_fields:
            # non-idempotent tools need a logical-operation key so duplicates can be recognised
            raise ValueError(f"non-idempotent tool '{self.name}' must declare key_fields")
        return self


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(alias="from")
    action: str
    target: str = Field(alias="to")


class StageRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str
    when: str


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    foreach: Optional[str] = None      # expand over a list in the task parameters


class FaultTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hook: str = Field(pattern=r"^(propose|execute|replay)$")
    tool: str
    occurrence: int = 1                # 0 = choose an occurrence at random (seeded)
    inject: Optional[str] = None       # E1: action substituted for the proposal
    detail: str = ""


class WorkflowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str
    version: str
    family: str
    description: str = ""
    environment: str
    states: List[str]
    initial: str
    final: List[str]
    transitions: List[Transition]
    stage_rules: List[StageRule]
    invariants: List[Condition] = Field(default_factory=list)
    completion: List[Condition] = Field(default_factory=list)
    tools: Dict[str, ToolContract]
    plan: List[PlanStep]
    fault_targets: Dict[str, FaultTarget]

    @field_validator("tools", mode="before")
    @classmethod
    def name_tools(cls, v):
        if isinstance(v, dict):
            for k, t in v.items():
                if isinstance(t, dict):
                    t.setdefault("name", k)
        return v

    @model_validator(mode="after")
    def validate_graph(self):
        states = set(self.states)
        if self.initial not in states:
            raise ValueError("initial state not declared")
        for f in self.final:
            if f not in states:
                raise ValueError(f"final state {f} not declared")
        seen = set()
        for t in self.transitions:
            if t.source not in states or t.target not in states:
                raise ValueError(f"transition {t.source}-{t.action}->{t.target} uses an undeclared state")
            if t.action not in self.tools:
                raise ValueError(f"transition uses unknown tool {t.action}")
            if (t.source, t.action) in seen:
                raise ValueError(f"non-deterministic transition from {t.source} on {t.action}")
            seen.add((t.source, t.action))
        for r in self.stage_rules:
            if r.stage not in states:
                raise ValueError(f"stage rule refers to unknown state {r.stage}")
        for name, tool in self.tools.items():
            for d in tool.requires:
                if d not in self.tools:
                    raise ValueError(f"tool {name} requires unknown tool {d}")
            if tool.compensation and tool.compensation not in self.tools:
                raise ValueError(f"tool {name} compensation {tool.compensation} is not a declared tool")
        for p in self.plan:
            if p.tool not in self.tools:
                raise ValueError(f"plan uses unknown tool {p.tool}")
        for f, ft in self.fault_targets.items():
            if f not in FaultType.__members__:
                raise ValueError(f"unknown fault type {f}")
        missing = set(FaultType.__members__) - set(self.fault_targets)
        if missing:
            raise ValueError(f"fault targets missing for {sorted(missing)}")
        return self


# --------------------------------------------------------------------------- runtime records
class Action(BaseModel):
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    role: str = "agent"
    origin: str = "agent"      # agent | fault_injector | recovery

    def label(self) -> str:
        a = ", ".join(f"{k}={v}" for k, v in self.args.items())
        return f"{self.tool}({a})"


class StepRecord(BaseModel):
    """Everything recorded for one verification decision (paper Section IV-F)."""
    step: int
    action: str
    args: Dict[str, Any] = Field(default_factory=dict)
    origin: str = "agent"
    stage_before: Optional[str] = None
    expected_stage: Optional[str] = None
    observed_stage: Optional[str] = None
    verdict: Verdict
    failed_predicate: Optional[str] = None
    message: Optional[str] = None
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    fault: Optional[str] = None
    probe: ProbeResult = ProbeResult.NOT_RUN
    recovery: Strategy = Strategy.NONE
    recovery_reason: Optional[str] = None
    admissible: List[str] = Field(default_factory=list)
    checkpoint: Optional[int] = None
    barrier: int = 0
    in_flight: List[str] = Field(default_factory=list)
    restoration_verified: Optional[bool] = None
    executed: bool = False
    oracle_unsafe: Optional[bool] = None
    timestamp: float = 0.0
    verification_ms: float = 0.0
    virtual_time: float = 0.0


class CheckpointEvent(BaseModel):
    step: int
    event: str                 # COMMIT | RESTORE | RESTORE_FAILED | BARRIER
    index: int
    stage: str
    history_len: int
    barrier: int
    verified: bool = True


class RunResult(BaseModel):
    workflow: str
    family: str
    configuration: str
    task_id: str
    seed: int
    fault: Optional[str] = None
    fault_variant: Optional[str] = None
    fault_fired: bool = False
    fault_step: Optional[int] = None
    completed: bool = False
    escalated: bool = False
    escalation_reason: Optional[str] = None
    final_stage: Optional[str] = None
    steps: List[StepRecord] = Field(default_factory=list)
    checkpoints: List[CheckpointEvent] = Field(default_factory=list)
    executed: int = 0
    tool_calls: int = 0
    state_reads: int = 0
    proposals: int = 0
    blocks: int = 0
    retries: int = 0
    unsafe_executed: int = 0
    unsafe_proposed: int = 0
    unsafe_blocked: int = 0
    correct_steps: int = 0
    false_flags: int = 0
    detected: bool = False
    duplicate_side_effects: int = 0
    virtual_time: float = 0.0
    verification_ms: float = 0.0
    recovery_latency: Optional[float] = None
    wall_ms: float = 0.0
