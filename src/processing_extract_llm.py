from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .llm_client import LLMClient
from .types import MetadataSpec
from .pdf_md import PageMD


@dataclass(frozen=True)
class ProcessingSubstepDecision:
    name: str
    methodology: str
    parameters: dict[str, str]
    qcs: list[str]
    evidence: str
    evidence_page: int
    confidence: float


# ---------------- Normalização determinística ----------------

_KEY_ALIASES: dict[str, str] = {
    "q": "Q",
    "q value": "Q",
    "q-factor": "Q",
    "q factor": "Q",

    "reference frequency": "ref_frequency_hz",
    "ref frequency": "ref_frequency_hz",
    "reference freq": "ref_frequency_hz",
    "fref": "ref_frequency_hz",

    "db limit": "limit_db",
    "limit db": "limit_db",
    "overcompensation limit": "limit_db",

    "domain": "domain",
    "fx domain": "domain",
    "f-x domain": "domain",
    "f x domain": "domain",

    "algorithm": "algorithm",
    "method": "method",
    "reference": "reference",
}

_RE_Q = re.compile(r"(?i)\bQ\s*=\s*(\d+(?:\.\d+)?)\b")
_RE_HZ = re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s*hz\b")
_RE_DB = re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s*db\b")


def _norm_key(k: str) -> str:
    k0 = (k or "").strip()
    if not k0:
        return ""
    k1 = " ".join(k0.split()).lower()
    return _KEY_ALIASES.get(k1, k1.replace(" ", "_"))


def _extract_hz(s: str) -> str | None:
    m = _RE_HZ.search(s or "")
    return m.group(1) if m else None


def _extract_db(s: str) -> str | None:
    m = _RE_DB.search(s or "")
    return m.group(1) if m else None


def _normalize_parameters(raw: Any, evidence: str) -> dict[str, str]:
    out: dict[str, str] = {}

    if isinstance(raw, dict):
        for k, v in raw.items():
            nk = _norm_key(str(k))
            if not nk:
                continue
            sv = str(v).strip() if v is not None else ""
            if not sv:
                continue

            if nk == "Q":
                m = _RE_Q.search(sv)
                if m:
                    sv = m.group(1)

            if nk.endswith("_hz"):
                hz = _extract_hz(sv)
                if hz:
                    sv = hz

            if nk.endswith("_db") or nk == "limit_db":
                db = _extract_db(sv)
                if db:
                    sv = db

            out[nk] = sv

    # fallback: inferir do evidence
    ev = evidence or ""

    if "Q" not in out:
        m = _RE_Q.search(ev)
        if m:
            out["Q"] = m.group(1)

    if "ref_frequency_hz" not in out:
        hz = _extract_hz(ev)
        if hz:
            out["ref_frequency_hz"] = hz

    if "limit_db" not in out:
        db = _extract_db(ev)
        if db:
            out["limit_db"] = db

    return out


# ---------------- Extração via LLM (substeps) ----------------

def extract_processing_substeps_from_pages(
    llm: LLMClient,
    spec: MetadataSpec,
    topic: str,
    pages_md: list[PageMD],
) -> list[ProcessingSubstepDecision]:
    pages_payload = [{"page": int(p.page), "md": p.md} for p in pages_md]

    system = (
        "You are an expert geophysics seismic processing report analyst.\n"
        "Extract ALL sub-steps described in the provided pages for the given parent processing step.\n"
        "Return ONLY a valid JSON object (no markdown).\n"
        "Be concise but specific.\n"
        "Normalize parameters when possible.\n"
        "Use canonical parameter keys when applicable: Q, ref_frequency_hz, limit_db, domain, algorithm, method, reference.\n"
        "For numeric parameters, return numeric strings WITHOUT units when the key implies the unit "
        "(ref_frequency_hz: '35', limit_db: '12', Q: '120').\n"
    )

    user = (
        "Parent step spec:\n"
        f"- parent_step_name: {spec.name}\n"
        f"- description: {spec.description}\n"
        f"- section_hints: {spec.section_hints}\n"
        f"- toc_topic_candidate: {topic}\n\n"
        "Pages:\n"
        f"{pages_payload}\n\n"
        "Return JSON with schema:\n"
        "{\n"
        '  "substeps": [\n'
        "    {\n"
        '      "name": "string",\n'
        '      "methodology": "string",\n'
        '      "parameters": {"key": "value"},\n'
        '      "qcs": ["string"],\n'
        '      "evidence": "string",\n'
        '      "evidence_page": 0,\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules:\n"
        "- Extract multiple substeps if present (e.g., within Preconditioning: Q-compensation, Denoising, Trim Statics).\n"
        "- evidence must be a short verbatim excerpt supporting the substep and its parameters.\n"
        "- evidence_page must be one of the provided pages.\n"
        "- confidence: 0..1.\n"
    )

    obj = llm.chat_json(system=system, user=user)
    raw = obj.get("substeps", []) or []

    allowed_pages = {int(p.page) for p in pages_md}
    out: list[ProcessingSubstepDecision] = []

    if not isinstance(raw, list):
        return out

    for it in raw:
        if not isinstance(it, dict):
            continue

        name = str(it.get("name", "") or "").strip()
        if not name:
            continue

        methodology = str(it.get("methodology", "") or "").strip()

        evidence = str(it.get("evidence", "") or "").strip()
        evidence_page = int(it.get("evidence_page", 0) or 0)
        if evidence_page not in allowed_pages:
            evidence_page = int(pages_md[0].page) if pages_md else 0

        confidence = float(it.get("confidence", 0.0) or 0.0)
        if confidence < 0.0:
            confidence = 0.0
        if confidence > 1.0:
            confidence = 1.0

        qcs_raw = it.get("qcs", []) or []
        qcs: list[str] = []
        if isinstance(qcs_raw, list):
            for x in qcs_raw:
                s = str(x).strip()
                if s:
                    qcs.append(s)

        parameters = _normalize_parameters(it.get("parameters", {}), evidence=evidence)

        out.append(
            ProcessingSubstepDecision(
                name=name,
                methodology=methodology,
                parameters=parameters,
                qcs=qcs,
                evidence=evidence,
                evidence_page=evidence_page,
                confidence=confidence,
            )
        )

    return out
