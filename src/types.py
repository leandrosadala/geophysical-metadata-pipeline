from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Strategy = Literal["regex", "hybrid", "llm"]


@dataclass(frozen=True)
class MetadataSpec:
    name: str
    description: str = ""
    section_hints: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    units_expected: list[str] = field(default_factory=list)
    strategy: Strategy = "hybrid"
    priority: int = 0
    accept_score: float = 0.85


@dataclass(frozen=True)
class ResolvedField:
    name: str
    valor: str
    evidencia: str
    pagina: int
    topico: str
    confianca: float
    is_null: bool
    source: Literal["regex", "llm", "none"] = "none"


@dataclass(frozen=True)
class TopicCandidate:
    topico: str
    page_start: int
    page_end: int


@dataclass(frozen=True)
class TocEntry:
    section_id: str | None
    title: str
    page_start: int
    page_end: int | None = None


@dataclass(frozen=True)
class ProcessingStep:
    # sub-etapa detectada (ex.: Q-compensation, Post Stack Denoising, Trim Statics)
    name: str

    # etapa “canônica” do catálogo que motivou a busca (obrigatório)
    parent_step: str

    # contexto do TOC/tópico onde apareceu
    topic_title: str
    topic_section_id: str

    # recorte de páginas enviado para extração (contexto de aplicação)
    page_start: int
    page_end: int

    methodology: str
    parameters: dict[str, str]
    qcs: list[str]

    evidence: str
    evidence_page: int

    confidence: float
    source: str  # "llm" | "regex" | "none"
