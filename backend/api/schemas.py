"""Pydantic request/response schemas for the REST API."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from backend.core.models import Configuration

VALID_CONFIGS = [c.value for c in Configuration]
VALID_CONDITIONS = ["clean", "E1", "E2", "E3", "E4", "E5", "E6"]


class HealthResponse(BaseModel):
    status: str
    version: str
    workflows: int
    database: str
    llm_adapter_configured: bool


class WorkflowSummary(BaseModel):
    name: str
    version: str
    family: str
    description: str
    sha256: str
    states: List[str]
    initial: str
    final: List[str]
    transitions: List[Dict[str, str]]
    tools: List[Dict[str, Any]]


class ExperimentRequest(BaseModel):
    mode: str = Field("quick", pattern=r"^(quick|standard|full)$")
    workflows: Optional[List[str]] = None
    configurations: Optional[List[str]] = None
    conditions: Optional[List[str]] = None
    n_templates: Optional[int] = Field(None, ge=1, le=50)
    seeds: Optional[List[int]] = None
    read_lag: float = Field(0.03, ge=0.0, le=0.5)
    background: Optional[bool] = None
    note: str = Field("", max_length=500)

    @field_validator("configurations")
    @classmethod
    def check_configs(cls, v):
        if v is not None:
            bad = [c for c in v if c not in VALID_CONFIGS]
            if bad:
                raise ValueError(f"unknown configurations {bad}; valid: {VALID_CONFIGS}")
        return v

    @field_validator("conditions")
    @classmethod
    def check_conditions(cls, v):
        if v is not None:
            bad = [c for c in v if c not in VALID_CONDITIONS]
            if bad:
                raise ValueError(f"unknown conditions {bad}; valid: {VALID_CONDITIONS}")
            if not v:
                raise ValueError("at least one condition is required")
        return v

    @field_validator("seeds")
    @classmethod
    def check_seeds(cls, v):
        if v is not None and (not v or len(v) > 10 or any(s < 0 for s in v)):
            raise ValueError("seeds must be 1-10 non-negative integers")
        return v


class ExperimentStarted(BaseModel):
    experiment_id: str
    status: str
    n_episodes: int
    detail: str


class SimulationRequest(BaseModel):
    workflow: str
    configuration: str = Configuration.SG.value
    fault: Optional[str] = None
    template: int = Field(0, ge=0, le=999)
    seed: int = Field(0, ge=0, le=999)
    read_lag: float = Field(0.0, ge=0.0, le=0.5)
    agent: str = Field("rule_based", pattern=r"^(rule_based|llm)$")
    persist: bool = True

    @field_validator("configuration")
    @classmethod
    def check_config(cls, v):
        if v not in VALID_CONFIGS:
            raise ValueError(f"unknown configuration {v}; valid: {VALID_CONFIGS}")
        return v

    @field_validator("fault")
    @classmethod
    def check_fault(cls, v):
        if v in (None, "", "clean", "none"):
            return None
        if v not in VALID_CONDITIONS[1:]:
            raise ValueError("fault must be one of E1..E6 or null")
        return v


class ErrorResponse(BaseModel):
    detail: str
