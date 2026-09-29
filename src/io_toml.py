from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import tomllib
import tomli_w

from .types import MetadataSpec, ResolvedField, TocEntry, ProcessingStep


def load_toml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    with p.open("rb") as f:
        return tomllib.load(f)


def load_catalog(catalog_path: str | Path) -> list[MetadataSpec]:
    data = load_toml(catalog_path)
    items = data.get("metadata", [])
    specs: list[MetadataSpec] = []

    for i, item in enumerate(items):
        if "name" not in item:
            raise ValueError(f"metadata[{i}] missing required field 'name'")

        specs.append(
            MetadataSpec(
                name=item["name"],
                description=item.get("description", ""),
                section_hints=item.get("section_hints", []) or [],
                patterns=item.get("patterns", []) or [],
                units_expected=item.get("units_expected", []) or [],
                strategy=item.get("strategy", "hybrid"),
                priority=int(item.get("priority", 0)),
                accept_score=float(item.get("accept_score", 0.85)),
            )
        )
    return specs


def write_result_toml(
    out_path: str | Path,
    file_name: str,
    pages_total: int,
    pipeline_version: str,
    results: list[ResolvedField],
) -> None:
    out = {
        "document": {
            "file_name": file_name,
            "pages_total": int(pages_total),
            "extraction_timestamp": dt.datetime.now(dt.timezone(dt.timedelta(hours=-3))).isoformat(),
            "pipeline_version": pipeline_version,
        },
        "results": [],
    }

    for r in results:
        out["results"].append(
            {
                "name": r.name,
                "valor": "" if r.is_null else r.valor,
                "evidencia": "" if r.is_null else r.evidencia,
                "pagina": 0 if r.is_null else int(r.pagina),
                "topico": "" if r.is_null else r.topico,
                "confianca": 0.0 if r.is_null else float(r.confianca),
                "is_null": bool(r.is_null),
            }
        )

    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("wb") as f:
        f.write(tomli_w.dumps(out).encode("utf-8"))


def write_processing_toml(
    out_path: str | Path,
    file_name: str,
    pages_total: int,
    pipeline_version: str,
    steps: list[ProcessingStep],
) -> None:
    out = {
        "document": {
            "file_name": file_name,
            "pages_total": int(pages_total),
            "extraction_timestamp": dt.datetime.now(dt.timezone(dt.timedelta(hours=-3))).isoformat(),
            "pipeline_version": pipeline_version,
        },
        "workflow_steps": [],
    }

    for s in steps:
        out["workflow_steps"].append(
            {
                "name": s.name,
                "parent_step": s.parent_step,
                "topic_title": s.topic_title,
                "topic_section_id": s.topic_section_id,
                "page_start": int(s.page_start),
                "page_end": int(s.page_end),
                "methodology": s.methodology,
                "parameters": s.parameters,
                "qcs": s.qcs,
                "evidence": s.evidence,
                "evidence_page": int(s.evidence_page),
                "confidence": float(s.confidence),
                "source": s.source,
            }
        )

    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    safe_out = _toml_sanitize(out)

    with p.open("wb") as f:
        f.write(tomli_w.dumps(safe_out).encode("utf-8"))


def write_toc_toml(
    out_path: str | Path,
    file_name: str,
    pages_total: int,
    toc_entries: list[TocEntry],
) -> None:
    out = {
        "document": {"file_name": file_name, "pages_total": int(pages_total)},
        "toc": [],
    }

    for t in toc_entries:
        out["toc"].append(
            {
                "section_id": t.section_id or "",
                "title": t.title,
                "page_start": int(t.page_start),
                "page_end": int(t.page_end or 0),
            }
        )

    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("wb") as f:
        f.write(tomli_w.dumps(out).encode("utf-8"))


def _toml_sanitize(obj):
    """
    TOML has no null. Convert None to empty string by default.
    Also sanitize recursively for dict/list.
    """
    if obj is None:
        return ""
    if isinstance(obj, dict):
        return {str(k): _toml_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_toml_sanitize(v) for v in obj]
    # primitives OK: str, int, float, bool
    return obj


def write_audit_toml(out_path: str | Path, audit: dict[str, Any]) -> None:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    safe_audit = _toml_sanitize(audit)

    with p.open("wb") as f:
        f.write(tomli_w.dumps(safe_audit).encode("utf-8"))
