from __future__ import annotations

from .llm_client import LLMClient
from .types import MetadataSpec, TocEntry, TopicCandidate


def pick_topics_for_metadata(
    llm: LLMClient,
    spec: MetadataSpec,
    toc: list[TocEntry],
    max_candidates: int = 3,
) -> list[TopicCandidate]:
    system = (
        "You map a metadata field to the most likely TOC topics where its value can be found.\n"
        "Return ONLY a valid JSON object.\n"
        "Output schema:\n"
        '{ "candidates": [ { "topico": string, "page_start": integer, "page_end": integer } ] }\n'
        "Rules:\n"
        f"- Return at most {max_candidates} candidates.\n"
        "- Only return topics that exist in the provided TOC list.\n"
        "- Prefer topics whose title strongly matches the provided hints.\n"
        "- Do not invent topics or page ranges.\n"
    )

    toc_serialized = [
        {
            "section_id": t.section_id,
            "title": t.title,
            "page_start": t.page_start,
            "page_end": t.page_end,
        }
        for t in toc
        if t.page_end is not None
    ]

    import json
    payload = {
        "metadata": {
            "name": spec.name,
            "description": spec.description,
            "section_hints": spec.section_hints,
            "units_expected": spec.units_expected,
        },
        "toc": toc_serialized,
    }
    user = json.dumps(payload, ensure_ascii=False)

    obj = llm.chat_json(system=system, user=user)
    cands = obj.get("candidates", [])
    if not isinstance(cands, list):
        return []

    out: list[TopicCandidate] = []
    for c in cands:
        if not isinstance(c, dict):
            continue
        topico = c.get("topico", "")
        try:
            ps = int(c.get("page_start"))
            pe = int(c.get("page_end"))
        except Exception:
            continue
        if isinstance(topico, str) and topico.strip():
            out.append(TopicCandidate(topico.strip(), ps, pe))

    return out[:max_candidates]
