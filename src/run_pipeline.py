from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .heuristics import try_extract_locally
from .io_toml import (
    load_catalog,
    load_toml,
    write_audit_toml,
    write_processing_toml,
    write_result_toml,
    write_toc_toml,
)
from .llm_client import LLMClient, OpenAIConfig
from .pdf_md import PageMarkdownStore
from .processing_extract_llm import extract_processing_substeps_from_pages
from .toc_hardening import harden_toc
from .toc_llm import extract_toc_llm, infer_page_ends
from .topic_match_llm import pick_topics_for_metadata
from .types import ProcessingStep, ResolvedField
from .value_extract_llm import extract_value_from_pages

logger = logging.getLogger(__name__)


def _select_first_n_pages_of_topic(
    page_start: int,
    page_end: int,
    max_pages: int,
    total_pages: int,
) -> list[int]:
    s = max(1, min(int(page_start), total_pages))
    e = max(1, min(int(page_end), total_pages))
    if e < s:
        e = s
    end = min(e, s + max_pages - 1)
    return list(range(s, end + 1))


def _preflight_ca_bundle(ca_bundle_path: Path) -> None:
    if not ca_bundle_path.exists():
        raise FileNotFoundError(f"CA bundle não encontrado: {ca_bundle_path}")
    if not ca_bundle_path.is_file():
        raise FileNotFoundError(f"CA bundle não é arquivo: {ca_bundle_path}")
    ca_bundle_path.read_bytes()


def _resolve_one_acquisition_metadata(
    *,
    llm: LLMClient,
    spec,
    candidates_payload: list[dict[str, Any]],
) -> tuple[ResolvedField, dict[str, Any]]:
    md_audit: dict[str, Any] = {
        "name": spec.name,
        "topics_tested": [],
        "resolved": None,
        "error": None,
    }

    try:
        resolved: ResolvedField | None = None

        if not candidates_payload:
            resolved = ResolvedField(
                name=spec.name,
                valor="",
                evidencia="",
                pagina=0,
                topico="",
                confianca=0.0,
                is_null=True,
                source="none",
            )
        else:
            for tc in candidates_payload:
                topic_audit: dict[str, Any] = {
                    "topico": tc["topico"],
                    "section_id": tc.get("section_id", ""),
                    "page_start": tc["page_start"],
                    "page_end": tc["page_end"],
                    "pages_sent": tc["pages_sent"],
                    "local": {"attempted": True, "hit": False, "score": None},
                    "llm": {"attempted": False, "hit": False, "score": None},
                }

                local = try_extract_locally(
                    spec=spec,
                    topic=tc["topico"],
                    pages=tc["pages_md"],
                    accept_score=float(getattr(spec, "accept_score", 0.85) or 0.85),
                )
                if local is not None:
                    topic_audit["local"]["hit"] = True
                    topic_audit["local"]["score"] = float(local.confianca)

                    resolved = ResolvedField(
                        name=spec.name,
                        valor=local.valor,
                        evidencia=local.evidencia,
                        pagina=int(local.pagina),
                        topico=local.topico,
                        confianca=float(local.confianca),
                        is_null=False,
                        source="regex",
                    )

                    md_audit["topics_tested"].append(topic_audit)
                    break

                topic_audit["llm"]["attempted"] = True
                decision = extract_value_from_pages(
                    llm=llm,
                    spec=spec,
                    topic=tc["topico"],
                    pages_md=tc["pages_md"],
                )

                if decision.valor is not None:
                    topic_audit["llm"]["hit"] = True
                    topic_audit["llm"]["score"] = float(decision.confianca)

                    resolved = ResolvedField(
                        name=spec.name,
                        valor=decision.valor,
                        evidencia=decision.evidencia or "",
                        pagina=int(decision.pagina or 0),
                        topico=decision.topico or tc["topico"],
                        confianca=float(decision.confianca),
                        is_null=False,
                        source="llm",
                    )

                    md_audit["topics_tested"].append(topic_audit)
                    break

                md_audit["topics_tested"].append(topic_audit)

            if resolved is None:
                resolved = ResolvedField(
                    name=spec.name,
                    valor="",
                    evidencia="",
                    pagina=0,
                    topico="",
                    confianca=0.0,
                    is_null=True,
                    source="none",
                )

    except Exception as e:
        md_audit["error"] = str(e)
        resolved = ResolvedField(
            name=spec.name,
            valor="",
            evidencia="",
            pagina=0,
            topico="",
            confianca=0.0,
            is_null=True,
            source="none",
        )

    md_audit["resolved"] = {
        "name": resolved.name,
        "is_null": resolved.is_null,
        "valor": resolved.valor,
        "pagina": resolved.pagina,
        "topico": resolved.topico,
        "confianca": resolved.confianca,
        "source": resolved.source,
    }
    return resolved, md_audit


def _resolve_one_processing_parent_step(
    *,
    llm: LLMClient,
    spec,
    candidates_payload: list[dict[str, Any]],
) -> tuple[list[ProcessingStep], dict[str, Any]]:
    """
    Resolve 1 item do catálogo de processing (parent_step) retornando uma lista de sub-etapas.
    Importante:
    - Não deduplica sub-etapas
    - Não para no primeiro tópico: processa TODOS os tópicos candidatos, pois cada um pode ter parâmetros diferentes
    """
    md_audit: dict[str, Any] = {
        "name": spec.name,
        "topics_tested": [],
        "resolved": None,
        "error": None,
    }

    steps: list[ProcessingStep] = []

    try:
        if not candidates_payload:
            md_audit["resolved"] = {"count_substeps": 0}
            return steps, md_audit

        for tc in candidates_payload:
            topic_audit: dict[str, Any] = {
                "topico": tc["topico"],
                "section_id": tc.get("section_id", ""),
                "page_start": tc["page_start"],
                "page_end": tc["page_end"],
                "pages_sent": tc["pages_sent"],
                "local": {"attempted": True, "hit": False, "score": None},
                "llm": {"attempted": True, "substeps_count": 0},
            }

            # heurística local só para “sinalizar” que tem match, mas não é decisória (LLM estrutura)
            local = try_extract_locally(
                spec=spec,
                topic=tc["topico"],
                pages=tc["pages_md"],
                accept_score=float(getattr(spec, "accept_score", 0.80) or 0.80),
            )
            if local is not None:
                topic_audit["local"]["hit"] = True
                topic_audit["local"]["score"] = float(local.confianca)

            # extrai sub-etapas (LLM)
            substeps = extract_processing_substeps_from_pages(
                llm=llm,
                spec=spec,
                topic=tc["topico"],
                pages_md=tc["pages_md"],
            )

            topic_audit["llm"]["substeps_count"] = int(len(substeps))

            # cria ProcessingStep (achatado) para cada substep retornada
            if substeps:
                page_start_ctx = int(tc["pages_sent"][0]) if tc["pages_sent"] else int(tc["page_start"])
                page_end_ctx = int(tc["pages_sent"][-1]) if tc["pages_sent"] else int(tc["page_end"])

                for ss in substeps:
                    steps.append(
                        ProcessingStep(
                            name=ss.name,                      # substep
                            parent_step=str(spec.name),         # obrigatório
                            topic_title=str(tc["topico"]),
                            topic_section_id=str(tc.get("section_id", "")),
                            page_start=page_start_ctx,
                            page_end=page_end_ctx,
                            methodology=ss.methodology,
                            parameters=ss.parameters,
                            qcs=ss.qcs,
                            evidence=ss.evidence,
                            evidence_page=int(ss.evidence_page),
                            confidence=float(ss.confidence),
                            source="llm",
                        )
                    )

            md_audit["topics_tested"].append(topic_audit)

        md_audit["resolved"] = {"count_substeps": int(len(steps))}
        return steps, md_audit

    except Exception as e:
        md_audit["error"] = str(e)
        md_audit["resolved"] = {"count_substeps": int(len(steps))}
        return steps, md_audit


def run(
    pdf_path: str | Path,
    outdir: str | Path,
    catalog_path: str | Path,
    openai_toml: str | Path,
    pipeline_toml: str | Path,
    pipeline_version: str = "0.1.0",
    *,
    mode: str,  # "acquisition" | "processing"
) -> None:
    pdf_path = Path(pdf_path)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if mode not in ("acquisition", "processing"):
        raise ValueError("mode must be 'acquisition' or 'processing'")

    logger.info("Iniciando pipeline (mode=%s)", mode)
    logger.info("PDF: %s", pdf_path)
    logger.info("Outdir: %s", outdir)
    logger.info("Catalog: %s", catalog_path)
    logger.info("OpenAI TOML: %s", openai_toml)
    logger.info("Pipeline TOML: %s", pipeline_toml)
    logger.info("Pipeline version: %s", pipeline_version)

    specs = load_catalog(catalog_path)
    logger.info("Itens no catálogo: %d", len(specs))

    openai_raw = load_toml(openai_toml)["openai"]

    # Resolve CA bundle path (relative to config file if needed)
    ca_bundle_path = Path(openai_raw["ca_bundle_path"])
    if not ca_bundle_path.is_absolute():
        ca_bundle_path = Path(openai_toml).parent / ca_bundle_path
    ca_bundle_path = ca_bundle_path.resolve()

    _preflight_ca_bundle(ca_bundle_path)
    logger.info("CA bundle (resolvido): %s", ca_bundle_path)

    cfg = OpenAIConfig(
        api_version=openai_raw["api_version"],
        azure_openai_base_url=openai_raw["azure_openai_base_url"],
        deployment=openai_raw["deployment"],
        ca_bundle_path=str(ca_bundle_path),
        timeout_seconds=int(openai_raw.get("timeout_seconds", 60)),
        temperature=float(openai_raw.get("temperature", 0.0)),
        max_retries=int(openai_raw.get("max_retries", 5)),
        retry_base_seconds=float(openai_raw.get("retry_base_seconds", 0.8)),
        retry_max_seconds=float(openai_raw.get("retry_max_seconds", 8.0)),
    )

    llm = LLMClient(cfg)

    pipe_raw = load_toml(pipeline_toml)["pipeline"]
    toc_scan_pages = int(pipe_raw.get("toc_scan_pages", 8))
    max_topic_candidates = int(pipe_raw.get("max_topic_candidates", 3))
    max_pages_per_metadata = int(pipe_raw.get("max_pages_per_metadata", 5))
    max_workers = int(pipe_raw.get("max_workers", 4))

    logger.info("Parâmetros do pipeline:")
    logger.info("- toc_scan_pages: %d", toc_scan_pages)
    logger.info("- max_topic_candidates: %d", max_topic_candidates)
    logger.info("- max_pages_per_metadata: %d", max_pages_per_metadata)
    logger.info("- max_workers: %d", max_workers)

    store = PageMarkdownStore(pdf_path)
    try:
        total_pages = store.total_pages
        logger.info("Total de páginas do PDF: %d", total_pages)

        audit: dict[str, Any] = {
            "document": {"file_name": pdf_path.name, "pages_total": total_pages},
            "mode": mode,
            "pipeline": {
                "toc_scan_pages": toc_scan_pages,
                "max_topic_candidates": max_topic_candidates,
                "max_pages_per_metadata": max_pages_per_metadata,
                "max_workers": max_workers,
            },
            "toc_stats": {},
            "toc_hardening": {},
            "metadata": [],
            "errors": [],
        }

        # 1) TOC
        toc_pages = list(range(1, min(toc_scan_pages, total_pages) + 1))
        logger.info("Extraindo TOC via LLM (páginas %s..%s)", toc_pages[0], toc_pages[-1])

        toc_pages_md = store.get_pages_md(toc_pages)
        toc_raw = extract_toc_llm(llm, toc_pages_md)
        toc = infer_page_ends(toc_raw, total_pages)

        toc, toc_hard_stats = harden_toc(toc, total_pages=total_pages)
        audit["toc_hardening"] = toc_hard_stats
        audit["toc_stats"] = {"entries": len(toc)}
        logger.info("TOC final: %d entradas", len(toc))

        write_toc_toml(
            out_path=outdir / "toc_inferred.toml",
            file_name=pdf_path.name,
            pages_total=total_pages,
            toc_entries=toc,
        )
        logger.info("Arquivo gerado: %s", outdir / "toc_inferred.toml")

        # 2) Preparação serial: topic picking + extração MD (cache)
        logger.info("Preparando jobs (serial): topic picking + extração MD")
        jobs: list[tuple[int, Any, list[dict[str, Any]]]] = []

        for idx, spec in enumerate(specs):
            logger.info("Preparando item (%d/%d): %s", idx + 1, len(specs), spec.name)
            try:
                topic_candidates = pick_topics_for_metadata(
                    llm=llm,
                    spec=spec,
                    toc=toc,
                    max_candidates=max_topic_candidates,
                )

                candidates_payload: list[dict[str, Any]] = []
                for tc in topic_candidates:
                    pages_to_read = _select_first_n_pages_of_topic(
                        page_start=tc.page_start,
                        page_end=tc.page_end,
                        max_pages=max_pages_per_metadata,
                        total_pages=total_pages,
                    )
                    slice_md = store.get_pages_md(pages_to_read)

                    candidates_payload.append(
                        {
                            "topico": tc.topico,
                            "section_id": getattr(tc, "section_id", "") if hasattr(tc, "section_id") else "",
                            "page_start": tc.page_start,
                            "page_end": tc.page_end,
                            "pages_sent": pages_to_read,
                            "pages_md": slice_md,
                        }
                    )

                jobs.append((idx, spec, candidates_payload))

            except Exception as e:
                logger.exception("Erro preparando item %s", spec.name)
                audit["errors"].append({"metadata": spec.name, "error": str(e)})
                jobs.append((idx, spec, []))

        # 3) Execução paralela
        if mode == "acquisition":
            logger.info("Executando resolução (acquisition) em paralelo: %d workers", max_workers)

            results_by_idx: dict[int, ResolvedField] = {}
            audit_by_idx: dict[int, dict[str, Any]] = {}

            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {}
                for idx, spec, payload in jobs:
                    fut = ex.submit(
                        _resolve_one_acquisition_metadata,
                        llm=llm,
                        spec=spec,
                        candidates_payload=payload,
                    )
                    futures[fut] = idx

                for fut in as_completed(futures):
                    idx = futures[fut]
                    resolved, md_audit = fut.result()
                    results_by_idx[idx] = resolved
                    audit_by_idx[idx] = md_audit

            results = [results_by_idx[i] for i in range(len(specs))]
            audit["metadata"] = [audit_by_idx[i] for i in range(len(specs))]

            write_result_toml(
                out_path=outdir / "extraction_result.toml",
                file_name=pdf_path.name,
                pages_total=total_pages,
                pipeline_version=pipeline_version,
                results=results,
            )
            logger.info("Arquivo gerado: %s", outdir / "extraction_result.toml")

        else:
            logger.info("Executando extração (processing: sub-etapas) em paralelo: %d workers", max_workers)

            steps_by_idx: dict[int, list[ProcessingStep]] = {}
            audit_by_idx: dict[int, dict[str, Any]] = {}

            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {}
                for idx, spec, payload in jobs:
                    fut = ex.submit(
                        _resolve_one_processing_parent_step,
                        llm=llm,
                        spec=spec,
                        candidates_payload=payload,
                    )
                    futures[fut] = idx

                for fut in as_completed(futures):
                    idx = futures[fut]
                    steps_list, md_audit = fut.result()
                    steps_by_idx[idx] = steps_list
                    audit_by_idx[idx] = md_audit

            # audit por spec na ordem original
            audit["metadata"] = [audit_by_idx[i] for i in range(len(specs))]

            # ACHATAR (sem dedupe)
            all_steps: list[ProcessingStep] = []
            for i in range(len(specs)):
                all_steps.extend(steps_by_idx.get(i, []))

            # ordenar por ordem de ocorrência (evidence_page primeiro; fallback page_start)
            def _ord_key(s: ProcessingStep) -> tuple[int, int, str, str]:
                ep = int(s.evidence_page or 0)
                ps = int(s.page_start or 0)
                # empurra zeros para o final
                primary = ep if ep > 0 else 10**9
                secondary = ps if ps > 0 else 10**9
                return (primary, secondary, s.topic_title, s.name)

            all_steps_sorted = sorted(all_steps, key=_ord_key)

            write_processing_toml(
                out_path=outdir / "processing_summary.toml",
                file_name=pdf_path.name,
                pages_total=total_pages,
                pipeline_version=pipeline_version,
                steps=all_steps_sorted,
            )
            logger.info("Arquivo gerado: %s", outdir / "processing_summary.toml")

        # 4) Audit sempre
        write_audit_toml(out_path=outdir / "run_audit.toml", audit=audit)
        logger.info("Arquivo gerado: %s", outdir / "run_audit.toml")

        logger.info("Pipeline finalizado com sucesso (mode=%s)", mode)

    finally:
        store.close()
        logger.info("PageMarkdownStore fechado")
