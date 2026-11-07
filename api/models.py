"""Pydantic-модели: конфигурации прогонов, этапы, результаты.

Контракт витрины и проверок — в task/docs/СХЕМА_ВИТРИНЫ.md и
task/checks/README.md. Здесь только то, что ходит между фронтом и API.
"""
from __future__ import annotations

import time
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator, ConfigDict

CHANGE_TYPES = ["SAME", "VALUE_CHANGED", "ATTRIBUTE_NEW", "ATTRIBUTE_LOST"]


# ---------- процедуры и проверки ----------

class ProcedureSource(BaseModel):
    """Источник пользовательской процедуры: встроенная / рабочая область / файл."""
    model_config = ConfigDict(extra='forbid')
    kind: Literal["builtin", "workspace", "file", "code"] = "builtin"
    code: str | None = Field(default=None, max_length=1048576)
    settings: dict[str, Any] | None = None  # JSON normalization settings v1
    name: str = "default"  # имя встроенной или файла в workspace / на диске


class CheckSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: ProcedureSource = Field(default_factory=ProcedureSource)
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class RunConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default="прогон", min_length=1, max_length=200)
    user: str = "anon"
    expected_app_ids: list[str] | None = None
    dataset: str  # путь к папке прогона либо demo://synthetic
    normalize: ProcedureSource = Field(default_factory=ProcedureSource)
    compare: ProcedureSource = Field(default_factory=ProcedureSource)
    facts: ProcedureSource = Field(default_factory=ProcedureSource)
    checks: list[CheckSpec] = Field(default_factory=list)
    llm_opinion: bool = True
    stage_order: list[str] = Field(default_factory=lambda: ['normalize', 'compare', 'facts', 'checks', 'llm'])
    # схема настроек из UI (читаемая форма): exclude, array_rules [{path, mode,
    # keys}], bucket {preset: none|example, rules: [{path_prefix, edges}]}
    # — переводится в normalize.settings (контракт v1) перед запуском
    normalization: dict[str, Any] = Field(default_factory=dict)

    def _translate_normalization(self) -> None:
        n = self.normalization or {}
        if not n or self.normalize.settings:
            return
        settings: dict[str, Any] = {'version': 1}
        if n.get('exclude'):
            settings['exclude'] = list(n['exclude'])
        arrays = {r['path']: {'mode': r.get('mode', 'field'), 'keys': list(r.get('keys', []))}
                  for r in n.get('array_rules', []) if r.get('path')}
        if arrays:
            settings['arrays'] = arrays
        b = n.get('bucket') or {}
        buckets: dict[str, list] = {}

        def intervals(edges):
            edges = sorted(float(x) for x in edges)
            out = []
            prev = None
            for e in edges:
                label = f"(-∞ - {e:g}]" if prev is None else f"({prev:g} - {e:g}]"
                out.append({'lower': prev, 'upper': e, 'label': label})
                prev = e
            out.append({'lower': prev, 'upper': None, 'label': f"({prev:g}+)"})
            return out

        if b.get('preset') == 'example':
            # пресет «как в примере задания» (см. pipeline.config.example_bucket)
            buckets['suffix:.value'] = [
                {'lower': 0, 'upper': .3, 'label': '(0 - 0.3]'},
                {'lower': .3, 'upper': .6, 'label': '(0.3 - 0.6]'},
                {'lower': .6, 'upper': .9, 'label': '(0.6 - 0.9]'},
                {'lower': .9, 'upper': 1, 'label': '(0.9 - 1.0]'}]
            buckets['suffix:.limit'] = [
                {'lower': 0, 'upper': 100000, 'label': '(0 - 100K]'},
                {'lower': 100000, 'upper': 250000, 'label': '(100K - 250K]'},
                {'lower': 250000, 'upper': 1000000, 'label': '(250K - 1M]'}]
        for r in b.get('rules') or []:
            if r.get('path_prefix') and r.get('edges'):
                buckets[r['path_prefix']] = intervals(r['edges'])
        if buckets:
            settings['buckets'] = buckets
        if len(settings) > 1:
            self.normalize.settings = settings

    @model_validator(mode='after')
    def validate_sequence(self):
        self._translate_normalization()
        if len(set(self.stage_order)) != len(self.stage_order):
            raise ValueError('Duplicate stage in stage_order')
        names = [n for n in self.stage_order if n != 'llm' or self.llm_opinion]
        required = {'normalize', 'compare', 'facts', 'checks'} | ({'llm'} if self.llm_opinion else set())
        if len(names) != len(set(names)) or set(names) != required or names[:3] != ['normalize', 'compare', 'facts']:
            raise ValueError('stage_order requires normalize → compare → facts, then checks and optionally llm, each once')
        from pipeline import config_from_settings
        config_from_settings(self.normalize.settings)
        return self


# ---------- ход прогона ----------

class Stage(BaseModel):
    name: str
    title: str = ""
    status: Literal["pending", "running", "done", "error", "skipped"] = "pending"
    started: Optional[float] = None
    finished: Optional[float] = None
    message: str = ""
    error: str = ""
    completed: int = 0
    total: int | None = None
    duration_seconds: float | None = None
    code_sha256: str = ""
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
    llm: Optional[dict[str, Any]] = None
    error: str = ""
    errors: list[dict[str, Any]] = Field(default_factory=list)
    check_executions: list[dict[str, Any]] = Field(default_factory=list)
    check_report_status: str = "PENDING"
    rerun_of: str | None = None
    active_check: dict[str, Any] | None = None
    quality: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    fact_rows: list[dict[str, Any]] = Field(default_factory=list)
    segment_details: list[dict[str, Any]] = Field(default_factory=list)


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


class PivotRequest(BaseModel):
    dimensions: list[str] = Field(default_factory=lambda: ['path', 'change_type'])
    filters: dict[str, Any] = Field(default_factory=dict)
    segment_filters: dict[str, Any] = Field(default_factory=dict)
    column_dimension: str | None = None
    limit: int = Field(default=5000, ge=1, le=10000)
    offset: int = Field(default=0, ge=0)
