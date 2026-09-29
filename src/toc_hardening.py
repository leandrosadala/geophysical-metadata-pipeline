from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import TocEntry


def _norm_title(s: str) -> str:
    return " ".join((s or "").split()).strip().lower()


def harden_toc(
    toc: list[TocEntry],
    total_pages: int,
    *,
    max_section_span_pages: int = 60,
) -> tuple[list[TocEntry], dict[str, Any]]:
    """
    Hardening/validação do TOC:
    - clamp page_start/page_end no range [1..total_pages]
    - remove entradas com page_start inválido
    - ordena por page_start (monotonicidade)
    - remove duplicatas (mesmo title normalizado + page_start)
    - valida/ajusta page_end (>= start, <= total_pages)
    - detecta gaps e spans absurdos (só loga; ajuste conservador)

    Retorna (toc_hardened, stats)
    """
    stats: dict[str, Any] = {
        "input_entries": len(toc),
        "dropped_invalid": 0,
        "dropped_duplicates": 0,
        "reordered": False,
        "clamped_pages": 0,
        "fixed_page_end": 0,
        "warnings": [],
    }

    cleaned: list[TocEntry] = []

    # 1) clamp + drop invalid
    for t in toc:
        try:
            ps = int(t.page_start)
        except Exception:
            stats["dropped_invalid"] += 1
            continue

        if ps < 1 or ps > total_pages:
            stats["dropped_invalid"] += 1
            continue

        pe = t.page_end
        pe_int: int | None
        if pe is None:
            pe_int = None
        else:
            try:
                pe_int = int(pe)
            except Exception:
                pe_int = None

        # clamp end if present
        if pe_int is not None:
            new_pe = max(1, min(pe_int, total_pages))
            if new_pe != pe_int:
                stats["clamped_pages"] += 1
            pe_int = new_pe

        cleaned.append(
            TocEntry(
                section_id=t.section_id,
                title=t.title,
                page_start=ps,
                page_end=pe_int,
            )
        )

    # 2) sort by page_start (monotonicidade)
    orig_starts = [t.page_start for t in cleaned]
    cleaned_sorted = sorted(cleaned, key=lambda x: (x.page_start, _norm_title(x.title)))
    stats["reordered"] = orig_starts != [t.page_start for t in cleaned_sorted]

    # 3) de-dup
    out: list[TocEntry] = []
    seen: set[tuple[str, int]] = set()
    for t in cleaned_sorted:
        key = (_norm_title(t.title), int(t.page_start))
        if key in seen:
            stats["dropped_duplicates"] += 1
            continue
        seen.add(key)
        out.append(t)

    # 4) fix page_end sanity (>= start)
    fixed: list[TocEntry] = []
    for t in out:
        if t.page_end is None:
            fixed.append(t)
            continue
        if int(t.page_end) < int(t.page_start):
            stats["fixed_page_end"] += 1
            fixed.append(
                TocEntry(
                    section_id=t.section_id,
                    title=t.title,
                    page_start=int(t.page_start),
                    page_end=int(t.page_start),
                )
            )
        else:
            fixed.append(t)

    # 5) warnings: gaps & spans absurdos (conservador: não corrige agressivamente)
    for i in range(len(fixed)):
        t = fixed[i]
        if t.page_end is not None:
            span = int(t.page_end) - int(t.page_start) + 1
            if span > max_section_span_pages:
                stats["warnings"].append(
                    {
                        "type": "span_too_large",
                        "title": t.title,
                        "page_start": t.page_start,
                        "page_end": t.page_end,
                        "span_pages": span,
                        "max_section_span_pages": max_section_span_pages,
                    }
                )

        if i + 1 < len(fixed):
            nxt = fixed[i + 1]
            gap = int(nxt.page_start) - int(t.page_start)
            if gap > (max_section_span_pages * 2):
                stats["warnings"].append(
                    {
                        "type": "gap_suspicious",
                        "from_title": t.title,
                        "from_page_start": t.page_start,
                        "to_title": nxt.title,
                        "to_page_start": nxt.page_start,
                        "gap_pages": gap,
                    }
                )

    stats["output_entries"] = len(fixed)
    return fixed, stats
