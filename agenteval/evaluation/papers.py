# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Exam paper library: loading, schema validation and consistency checks."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from agenteval.config import content_dir

from .models import ExamPaper


class PaperError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Exam paper validation failed:\n  " + "\n  ".join(errors))


def default_exams_dir() -> Path:
    return content_dir("exams", "AGENTEVAL_EXAMS_DIR")


def _validator() -> Draft202012Validator:
    text = resources.files("agenteval").joinpath("schemas/exam.schema.json").read_text(encoding="utf-8")
    return Draft202012Validator(json.loads(text))


def _consistency(p: ExamPaper) -> list[str]:
    errs: list[str] = []
    dims = {d.id for d in p.dimensions}
    tools = {t.get("name") for t in p.tools}
    seen: set[str] = set()
    for c in p.cases:
        if c.id in seen:
            errs.append(f"{p.id}: duplicate case ID {c.id}")
        seen.add(c.id)
        rub_ids = [r.id for r in c.rubric]
        if len(rub_ids) != len(set(rub_ids)):
            errs.append(f"{p.id}/{c.id}: rubric id 重复")
        for i, t in enumerate(c.turns):
            for a in t.expect:
                if a.dimension not in dims:
                    errs.append(f"{p.id}/{c.id} turn {i + 1}: unknown dimension {a.dimension}")
                if a.type in ("tool_called", "tool_not_called") and tools and a.value not in tools:
                    errs.append(f"{p.id}/{c.id} turn {i + 1}: tool not declared by the paper {a.value}")
                if a.type in ("contains_any", "contains_all") and not isinstance(a.value, list):
                    errs.append(f"{p.id}/{c.id} turn {i + 1}: value of {a.type} must be a list")
        for r in c.rubric:
            if r.dimension not in dims:
                errs.append(f"{p.id}/{c.id}: rubric {r.id} has unknown dimension {r.dimension}")
        if not c.rubric and not any(t.expect for t in c.turns):
            errs.append(f"{p.id}/{c.id}: no assertions or rubric items")
    return errs


def load_paper(path: str | Path) -> ExamPaper:
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    errs = [f"{path.name} {'/'.join(map(str, e.path)) or '<root>'}: {e.message}"
            for e in sorted(_validator().iter_errors(doc), key=lambda e: list(e.path))]
    if errs:
        raise PaperError(errs)
    paper = ExamPaper.model_validate(doc)
    errs = _consistency(paper)
    if errs:
        raise PaperError(errs)
    return paper


def load_papers(directory: str | Path | None = None) -> dict[str, ExamPaper]:
    directory = Path(directory or default_exams_dir())
    papers: dict[str, ExamPaper] = {}
    errors: list[str] = []
    for f in sorted(directory.rglob("*.y*ml")):
        try:
            p = load_paper(f)
        except PaperError as e:
            errors.extend(e.errors)
            continue
        if p.id in papers:
            errors.append(f"{f.name}: 试卷 ID 重复 {p.id}")
        papers[p.id] = p
    if errors:
        raise PaperError(errors)
    return papers


def resolve_paper(ref: str, directory: str | Path | None = None) -> ExamPaper:
    """ref may be a paper file path or a paper ID from the library."""
    p = Path(ref)
    if p.suffix in (".yaml", ".yml") and p.exists():
        return load_paper(p)
    papers = load_papers(directory)
    if ref not in papers:
        raise PaperError([f"Exam paper not found: {ref} (available: {', '.join(sorted(papers))})"])
    return papers[ref]
