from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = 2

BuildStatus = Literal["passed", "failed", "skipped", "partial", "error", "not_run"]
DeployShape = Literal["static", "static_build", "server", "multi", "existing_docker"]


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class DeployUnitArtifacts(BaseModel):
    railpack_plan: Optional[Dict[str, Any]] = None
    railpack_json: Optional[Dict[str, Any]] = None


class DeployUnit(BaseModel):
    name: str
    root: str
    type: str
    provider: str = ""
    framework: Optional[str] = None
    port: int = 8000
    artifacts: DeployUnitArtifacts = Field(default_factory=DeployUnitArtifacts)


class RepairAttempt(BaseModel):
    attempt: int
    unit_name: str
    diagnosis: str = ""
    patch: Dict[str, Any] = Field(default_factory=dict)
    railpack_json_after_merge: Optional[Dict[str, Any]] = None
    build_log_excerpt: str = ""
    build_exit_code: Optional[int] = None
    duration_seconds: float = 0.0
    result: str = "failed"


class PipelineTraceEntry(BaseModel):
    node: str
    status: str
    duration_ms: int = 0
    error: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class BuildVerification(BaseModel):
    backend: str = "railpack"
    status: str = "not_run"
    message: str = ""
    attempts: int = 0
    duration_seconds: float = 0.0
    log_excerpt: str = ""


class AnalyzeResponse(BaseModel):
    schema_version: int = SCHEMA_VERSION
    response_id: Optional[str] = None
    commit_sha: str = "unknown"
    package_path: str = "."
    deploy_shape: DeployShape = "server"
    railpack_version: Optional[str] = None
    workflow_version: Optional[str] = None
    build_status: BuildStatus = "not_run"
    deploy_units: List[DeployUnit] = Field(default_factory=list)
    deploy_briefing: str = ""
    build_verification: BuildVerification = Field(default_factory=BuildVerification)
    repair_history: List[RepairAttempt] = Field(default_factory=list)
    pipeline_trace: List[PipelineTraceEntry] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    llm_outputs: Dict[str, Any] = Field(default_factory=dict)
    inputs_snapshot: Dict[str, Any] = Field(default_factory=dict)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
