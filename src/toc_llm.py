from __future__ import annotations

from dataclasses import dataclass

from .llm_client import LLMClient
from .pdf_md import PageMD
from .types import TocEntry


def infer_page_ends(entries: list[TocEntry], total_pages: int) -> list[TocEntry]:
    if not entries:
        return []

    entries_sorted = sorted(entries, key=lambda e: (e.page_start, e.section_id or "", e.title))

    out: list[TocEntry] = []
    for i, cur in enumerate(entries_sorted):
        if i < len(entries_sorted) - 1:
            nxt = entries_sorted[i + 1]
            end = max(cur.page_start, nxt.page_start - 1)
        else:
            end = total_pages
        out.append(TocEntry(cur.section_id, cur.title, cur.page_start, end))
    return out


def extract_toc_llm(llm: LLMClient, pages_md: list[PageMD]) -> list[TocEntry]:
    system = (
        "You are extracting the Table of Contents (TOC) from a technical report.\n"
        "Return ONLY a valid JSON object (no markdown, no extra text).\n"
        "Output schema:\n"
        '{ "found": boolean, "toc": [ { "section_id": string|null, "title": string, "page_start": integer } ] }\n'
        "Rules:\n"
        "- If TOC not present in the provided pages, set found=false and toc=[].\n"
        "- page_start must be the physical page number referenced in the TOC entry.\n"
        "- Ignore lines without a clear numeric page.\n"
        "- Keep titles as they appear (no paraphrasing).\n"
    )

    payload = {
        "pages": [{"page": p.page, "md": p.md[:12000]} for p in pages_md]
    }

    import json
    user = json.dumps(payload, ensure_ascii=False)

    obj = llm.chat_json(system=system, user=user)

    found = bool(obj.get("found", False))
    toc = obj.get("toc", [])
    if not found or not isinstance(toc, list):
        return []

    entries: list[TocEntry] = []
    for item in toc:
        if not isinstance(item, dict):
            continue
        title = item.get("title", "")
        page_start = item.get("page_start", None)
        section_id = item.get("section_id", None)

        if not isinstance(title, str) or not title.strip():
            continue
        try:
            ps = int(page_start)
        except Exception:
            continue

        sid = section_id if isinstance(section_id, str) and section_id.strip() else None
        entries.append(TocEntry(sid, title.strip(), ps, None))

    uniq = {}
    for e in entries:
        uniq[(e.section_id, e.title, e.page_start)] = e
    return list(uniq.values())
