from __future__ import annotations

from dataclasses import dataclass

from .llm_client import LLMClient
from .pdf_md import PageMD
from .types import MetadataSpec


@dataclass(frozen=True)
class ValueDecision:
    valor: str | None
    evidencia: str | None
    pagina: int
    topico: str | None
    confianca: float


def extract_value_from_pages(
    llm: LLMClient,
    spec: MetadataSpec,
    topic: str,
    pages_md: list[PageMD],
) -> ValueDecision:
    system = (
        "You extract ONE single value for a requested metadata field from the provided report pages.\n"
        "Return ONLY a valid JSON object.\n"
        "Output schema:\n"
        '{ "valor": string|null, "evidencia": string|null, "pagina": integer, "topico": string|null, "confianca": float }\n'
        "Rules:\n"
        "- If the value is not explicitly present in the provided pages, return valor=null and evidencia=null.\n"
        "- evidencia must be copied verbatim from the page text.\n"
        "- pagina must be the physical page number where the evidence appears.\n"
        "- confianca must be in [0,1].\n"
        "- Do not guess.\n"
    )

    import json
    payload = {
        "metadata": {
            "name": spec.name,
            "description": spec.description,
            "units_expected": spec.units_expected,
        },
        "topic": topic,
        "pages": [{"page": p.page, "md": p.md[:12000]} for p in pages_md],
        "notes": "Extract only from these pages. Do not guess."
    }
    user = json.dumps(payload, ensure_ascii=False)

    obj = llm.chat_json(system=system, user=user)

    valor = obj.get("valor", None)
    evidencia = obj.get("evidencia", None)
    topico = obj.get("topico", None)
    pagina = obj.get("pagina", 0)
    confianca = obj.get("confianca", 0.0)

    if valor is not None and not isinstance(valor, str):
        valor = str(valor)
    if evidencia is not None and not isinstance(evidencia, str):
        evidencia = str(evidencia)
    if topico is not None and not isinstance(topico, str):
        topico = str(topico)

    try:
        pagina_i = int(pagina)
    except Exception:
        pagina_i = 0

    try:
        conf_f = float(confianca)
    except Exception:
        conf_f = 0.0

    conf_f = max(0.0, min(1.0, conf_f))

    if valor is None:
        return ValueDecision(valor=None, evidencia=None, pagina=0, topico=None, confianca=0.0)

    return ValueDecision(
        valor=valor.strip(),
        evidencia=(evidencia or "").strip(),
        pagina=pagina_i,
        topico=topico.strip() if isinstance(topico, str) and topico.strip() else None,
        confianca=conf_f,
    )
