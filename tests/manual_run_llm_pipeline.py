from __future__ import annotations

import logging
from pathlib import Path
import sys

from geophysical_metadata_pipeline.run_pipeline import run


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    repo_root = Path.cwd().resolve()

    pdf_path = repo_root / "data" / "Relatorio_Aquisicao_Streamer.pdf"
    outdir = repo_root / "out"

    catalog_path = repo_root / "config" / "metadata_catalog.toml"
    openai_toml = repo_root / "config" / "openai.toml"
    pipeline_toml = repo_root / "config" / "pipeline.toml"

    for p, label in [
        (pdf_path, "PDF"),
        (catalog_path, "Catálogo (metadata_catalog.toml)"),
        (openai_toml, "Config OpenAI/Azure (openai.toml)"),
        (pipeline_toml, "Config Pipeline (pipeline.toml)"),
    ]:
        if not p.exists():
            raise FileNotFoundError(f"{label} não encontrado: {p}")

    outdir.mkdir(parents=True, exist_ok=True)

    run(
        pdf_path=pdf_path,
        outdir=outdir,
        catalog_path=catalog_path,
        openai_toml=openai_toml,
        pipeline_toml=pipeline_toml,
        pipeline_version="0.1.0",
    )

    print("\nOK. Arquivos gerados em:")
    print(f"- {outdir / 'toc_inferred.toml'}")
    print(f"- {outdir / 'extraction_result.toml'}")
    print(f"- {outdir / 'run_audit.toml'}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\nERRO: {exc}\n", file=sys.stderr)
        raise
