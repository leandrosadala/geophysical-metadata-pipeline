from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from .types import MetadataSpec
from .pdf_md import PageMD

@dataclass(frozen=True)
class LocalDecision:
    valor: str
    evidencia: str
    pagina: int
    confianca: float
    topico: str


def _normalize_spaces(s: str) -> str:
    return re.sub(r"[ \t]+", " ", s).strip()


def _has_expected_unit(value: str, units_expected: list[str]) -> bool:
    if not units_expected:
        return True
    v = value.lower()
    return any(u.lower() in v for u in units_expected)


def _extract_with_regex_patterns(
    spec: MetadataSpec,
    pages: list[PageMD],
    topic: str,
) -> LocalDecision | None:
    """
    Usa spec.patterns como regex para capturar valor.
    Convenção sugerida: patterns com grupo nomeado (?P<value>...).
    Ex.: r"Sample Rate\\s*[:=]\\s*(?P<value>\\d+(?:\\.\\d+)?\\s*ms)"
    """
    patterns: list[str] = spec.patterns or []
    if not patterns:
        return None

    compiled = []
    for pat in patterns:
        try:
            compiled.append(re.compile(pat, flags=re.IGNORECASE | re.MULTILINE))
        except re.error:
            # pattern inválido: ignora (não quebra o pipeline)
            continue

    if not compiled:
        return None

    for p in pages:
        text = p.md or ""
        for rgx in compiled:
            m = rgx.search(text)
            if not m:
                continue

            value = m.groupdict().get("value")
            if not value:
                # fallback: primeiro grupo capturado
                if m.groups():
                    value = m.group(1)
            if not value:
                continue

            value = _normalize_spaces(value)
            if not _has_expected_unit(value, spec.units_expected):
                continue

            evidencia = _normalize_spaces(m.group(0))
            return LocalDecision(
                valor=value,
                evidencia=evidencia[:800],  # limita evidência
                pagina=int(p.page),
                confianca=0.92,  # alta: veio de pattern específico do catálogo
                topico=topic,
            )

    return None


def _candidate_labels(spec: MetadataSpec) -> list[str]:
    """
    Labels prováveis para buscar em padrões genéricos.
    Inclui o próprio name e hints.
    """
    labels = [spec.name]
    labels.extend(spec.section_hints or [])
    # remove vazios/duplicatas
    out = []
    seen = set()
    for x in labels:
        x = (x or "").strip()
        if not x:
            continue
        k = x.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def _extract_generic_label_value(
    spec: MetadataSpec,
    pages: list[PageMD],
    topic: str,
) -> LocalDecision | None:
    """
    Padrão genérico:
      Label : Value
      Label = Value
    Captura 'value' em uma linha.
    """
    labels = _candidate_labels(spec)
    if not labels:
        return None

    # constrói alternância segura
    labels_alt = "|".join(re.escape(l) for l in labels)
    rgx = re.compile(
        rf"(?im)^\s*(?:{labels_alt})\s*[:=]\s*(?P<value>.+?)\s*$"
    )

    for p in pages:
        text = p.md or ""
        m = rgx.search(text)
        if not m:
            continue

        value = _normalize_spaces(m.group("value"))
        if not value:
            continue
        if not _has_expected_unit(value, spec.units_expected):
            # se não tem units_expected, passa; se tem, exige bater
            continue

        evidencia = _normalize_spaces(m.group(0))
        return LocalDecision(
            valor=value,
            evidencia=evidencia[:800],
            pagina=int(p.page),
            confianca=0.80,  # menor que pattern específico
            topico=topic,
        )

    return None


def try_extract_locally(
    spec: MetadataSpec,
    topic: str,
    pages: list[PageMD],
    accept_score: float | None = None,
) -> LocalDecision | None:
    """
    Tenta resolver localmente. Se encontrar, aplica threshold.
    accept_score default: spec.accept_score (ou 0.85)
    """
    threshold = float(accept_score if accept_score is not None else (spec.accept_score or 0.85))

    # 1) patterns específicos do catálogo
    d = _extract_with_regex_patterns(spec, pages, topic)
    if d and d.confianca >= threshold:
        return d

    # 2) label/value genérico
    d = _extract_generic_label_value(spec, pages, topic)
    if d and d.confianca >= threshold:
        return d

    return None
