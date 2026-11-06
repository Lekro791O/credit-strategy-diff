"""Pydantic-модели: конфигурации прогонов, этапы, результаты.

Контракт витрины и проверок — в task/docs/СХЕМА_ВИТРИНЫ.md и
task/checks/README.md. Здесь только то, что ходит между фронтом и API.
"""
from __future__ import annotations

import time
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

CHANGE_TYPES = ["SAME", "VALUE_CHANGED", "ATTRIBUTE_NEW", "ATTRIBUTE_LOST"]


# ---------- процедуры и проверки ----------

class ProcedureSource(BaseModel):
    """Источник пользовательской процедуры: встроенная / рабочая область / файл."""
    kind: Literal["builtin", "workspace", "file"] = "builtin"
    name: str = "default"  # имя встроенной или файла в workspace / на диске


class CheckSpec(BaseModel):
    source: ProcedureSource = Field(default_factory=ProcedureSource)
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class RunConfig(BaseModel):
    name: str = "прогон"
    user: str = "anon"
    dataset: str  # путь к папке прогона либо demo://synthetic
    # последовательность этапов: normalize, compare, facts, checks (, llm)
    stage_order: list[str] = Field(
        default_factory=lambda: ["normalize", "compare", "facts", "checks", "llm"])
    normalize: ProcedureSource = Field(default_factory=ProcedureSource)
    compare: ProcedureSource = Field(default_factory=ProcedureSource)
    facts: ProcedureSource = Field(default_factory=ProcedureSource)
    # настройки нормализации (контракт pipeline.Config): exclude, array_rules,
    # aliases, bucket {preset: none|example, rules: [{path_prefix, edges: []}]}
    normalization: dict[str, Any] = Field(default_factory=dict)
    checks: list[CheckSpec] = Field(default_factory=list)
    llm_opinion: bool = True


# ---------- ход прогона ----------

class Stage(BaseModel):
    name: str
    title: str = ""
    status: Literal["pending", "running", "done", "error", "skipped"] = "pending"
    started: Optional[float] = None
    finished: Optional[float] = None
    message: str = ""
    error: str = ""  # краткая диагностика при status=error
    # откуда взят код этапа и его снапшот — из журнала можно провалиться в код
    code_ref: str = ""
    code_snapshot: str = ""


class RunSummary(BaseModel):
    total_apps: int = 0
    apps_with_diffs: int = 0        # заявки с хотя бы одним различием
    apps_decision_changed: int = 0  # сменилось итоговое решение
    decision_before: dict[str, int] = Field(default_factory=dict)
    decision_after: dict[str, int] = Field(default_factory=dict)


class Run(BaseModel):
    id: str
    config: RunConfig
    status: Literal["pending", "running", "done", "error"] = "pending"
    created: float = Field(default_factory=time.time)
    stages: list[Stage] = Field(default_factory=list)
    summary: Optional[RunSummary] = None
    check_rows: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)  # диагностика проверок
    llm: Optional[dict[str, Any]] = None
    error: str = ""


# ---------- факты и drill-down ----------

class FactRow(BaseModel):
    dims: dict[str, str]
    cnt_rows: int
    cnt_apps: int


class AppRow(BaseModel):
    """Строка витрины по одному атрибуту одной заявки (для drill-down)."""
    APP_ID: str
    PATH: str
    KEY: str
    ARRAY_KEY: str = ""
    VALUE_BEFORE: str = ""
    VALUE_AFTER: str = ""
    ADJUSTED_BEFORE: str = ""
    ADJUSTED_AFTER: str = ""
    DIFF_FLAG: int = 0
    CHANGE_TYPE: str = "SAME"
