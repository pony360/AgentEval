# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""AI agent evaluation: models for papers, cases and results."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

AssertionType = Literal[
    "contains", "contains_any", "contains_all", "not_contains",
    "regex", "not_regex",
    "tool_called", "tool_not_called",
    "max_latency_ms", "min_chars", "max_chars",
]


# ---------------- Exam papers ----------------

class Dimension(BaseModel):
    id: str
    name: str
    weight: float
    description: str = ""


class Assertion(BaseModel):
    type: AssertionType
    value: Any = None
    dimension: str
    weight: float = 1.0
    critical: bool = False          # critical: failing it scores the whole case 0 (veto)
    args_contains: dict[str, Any] | None = None  # optional argument check for tool_called
    description: str = ""


class Turn(BaseModel):
    user: str
    expect: list[Assertion] = Field(default_factory=list)


class RubricItem(BaseModel):
    id: str
    dimension: str
    criterion: str
    weight: float = 1.0
    # Reference signal words for the offline heuristic judge (LLM judges use them as reference points)
    signals: list[str] = Field(default_factory=list)


class Case(BaseModel):
    id: str
    title: str
    category: str
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    weight: float = 1.0
    tags: list[str] = Field(default_factory=list)
    tool_mocks: dict[str, Any] = Field(default_factory=dict)
    turns: list[Turn]
    rubric: list[RubricItem] = Field(default_factory=list)


class ExamPaper(BaseModel):
    id: str
    title: str
    domain: str
    version: str
    language: str = "en"
    description: str = ""
    passing_score: float = 75.0
    case_passing_score: float = 70.0
    dimensions: list[Dimension]
    context: str = ""               # Business context (e.g. policies, FAQ); optionally injected into the agent's system prompt
    tools: list[dict[str, Any]] = Field(default_factory=list)  # OpenAI function format
    cases: list[Case]


# ---------------- Run results ----------------

class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentReply(BaseModel):
    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    latency_ms: float = 0.0


class TranscriptEntry(BaseModel):
    role: Literal["user", "agent"]
    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    latency_ms: float | None = None


class AssertionResult(BaseModel):
    turn: int
    type: str
    value: Any = None
    dimension: str
    weight: float
    critical: bool
    passed: bool
    detail: str = ""
    detail_code: str = ""
    detail_params: dict[str, Any] = Field(default_factory=dict)


class RubricResult(BaseModel):
    id: str
    dimension: str
    criterion: str
    weight: float
    score: int | None = None        # 1–5; None means the judge failed
    rationale: str = ""             # LLM judge's verbatim rationale, or the default Chinese text of a system message
    rationale_code: str = ""        # system-generated rationales (heuristic/errors) use a code for multilingual rendering
    rationale_params: dict[str, Any] = Field(default_factory=dict)


class CaseResult(BaseModel):
    id: str
    title: str
    category: str
    difficulty: str
    weight: float
    score: float = 0.0
    passed: bool = False
    critical_failures: list[str] = Field(default_factory=list)
    error: str | None = None
    error_code: str = ""
    error_params: dict[str, Any] = Field(default_factory=dict)
    transcript: list[TranscriptEntry] = Field(default_factory=list)
    assertions: list[AssertionResult] = Field(default_factory=list)
    rubric: list[RubricResult] = Field(default_factory=list)


class DimensionScore(BaseModel):
    id: str
    name: str
    weight: float
    score: float | None = None      # None means the dimension has no scorable items
    items: int = 0


class EvalStats(BaseModel):
    cases: int = 0
    cases_passed: int = 0
    pass_rate: float = 0.0
    critical_failures: int = 0
    errors: int = 0
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None


class EvalRun(BaseModel):
    run_id: str
    paper_id: str
    paper_title: str
    paper_version: str
    agent_name: str
    judge: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    overall_score: float = 0.0
    grade: str = "F"
    passed: bool = False
    passing_score: float = 75.0
    dimensions: list[DimensionScore] = Field(default_factory=list)
    cases: list[CaseResult] = Field(default_factory=list)
    stats: EvalStats = Field(default_factory=EvalStats)
