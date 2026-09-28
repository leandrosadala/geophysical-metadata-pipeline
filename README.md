# Geophysical Metadata Pipeline (LLM-centric)

Pipeline para extração de metadados de relatórios técnicos geofísicos em PDF usando:

- `pymupdf4llm` para extrair conteúdo (texto + tabelas) por página em Markdown (**lazy + cache**)
- LLM via **Azure OpenAI (chat.completions)** para:
  1) extrair o TOC (“Contents”)
  2) inferir intervalos por tópico
  3) mapear metadados → tópicos candidatos
  4) extrair **valor + evidência + página + confiança** dentro de um limite estrito de páginas

## Estrutura do repositório

- `config/`
  - `openai.toml`: configuração de Azure OpenAI (gateway Petrobras) + CA bundle
  - `pipeline.toml`: parâmetros do pipeline (inclui paralelismo)
  - `metadata_catalog.toml`: catálogo de metadados (hints, patterns, unidades, thresholds)
  - `processing_catalog.toml`: catálogo de etapas para relatórios de processamento (modo `processing`)
- `data/Relatorio_Aquisicao_Streamer.pdf`: PDF de exemplo (aquisição)
- `out/`: outputs gerados
  - `toc_inferred.toml`
  - `extraction_result.toml`
  - `run_audit.toml`
  - `processing_summary.toml` (somente quando `--mode processing`)
- `src/geophysical_metadata_pipeline/`: código fonte
- `tests/manual_run_llm_pipeline.py`: runner manual com logging
- `ca_petrobras.pem`: CA bundle (referenciado por `config/openai.toml`)

## Principais features (estado atual)

- Extração **lazy** de Markdown: converte apenas as páginas necessárias (TOC + páginas por metadado)
- **Cache por página** em memória (evita recomputar páginas reutilizadas)
- Heurística local antes do LLM (**regex / “label: value”**) para reduzir custo/tempo
- **Hardening do TOC**: ordenação/limpeza/clamp e emissão de warnings (sem correção agressiva)
- **Paralelismo controlado** na fase de extração por metadado (`max_workers`)
- Retry/backoff (configurável) no `LLMClient` para robustez em falhas transitórias

## Saídas

O pipeline gera arquivos TOML no diretório `out/` (ou no `--outdir` informado):

- `toc_inferred.toml`: TOC inferido e intervalos de páginas por seção/tópico
- `run_audit.toml`: auditoria completa (tópicos testados, páginas enviadas, método `regex`/`llm`, warnings do TOC, erros)

No modo `acquisition` (padrão), também gera:

- `extraction_result.toml`: resultados finais por metadado (`valor`, `evidencia`, `pagina`, `topico`, `confianca`, `is_null`)

No modo `processing`, também gera:

- `processing_summary.toml`: lista estruturada de etapas do fluxo (`workflow_steps`) com metodologia, parâmetros, QCs e evidências

## Requisitos

- Python >= 3.12
- Variável de ambiente `OPENAI_API_KEY`
- Acesso ao endpoint Azure OpenAI via gateway Petrobras
- `ca_petrobras.pem` presente (conforme `config/openai.toml`)

## Instalação

```bash
python -m venv .venv

# Windows:
# .venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt
pip install -e .
```

## Configuração

#### 1) API Key

Defina OPENAI_API_KEY no ambiente.

#### 2) Azure OpenAI (via gateway Petrobras) + CA bundle

Edite config/openai.toml:

```toml
[openai]
api_version = "2024-10-21"
azure_openai_base_url = "https://apit.petrobras.com.br/ia/openai/v1/openai-azure/openai"
deployment = "gpt-4o-petrobras"

# Caminho relativo ao diretório do TOML (config/)
ca_bundle_path = "../ca_petrobras.pem"

timeout_seconds = 60
temperature = 0.0

# (Opcional, recomendado para paralelismo)
max_retries = 5
retry_base_seconds = 0.6
retry_max_seconds = 6.0
```
Observação: ca_bundle_path é resolvido relativo ao config/openai.toml. Portanto ../ca_petrobras.pem aponta para ca_petrobras.pem na raiz do repositório.

#### 3) Catálogo de metadados

Edite config/metadata_catalog.toml. O catálogo define:
- name
- description
- section_hints (ajuda o LLM e a heurística local)
- patterns (regex de captura; recomendado usar (?P<value>...))
- units_expected (validação simples)
- accept_score (threshold mínimo para aceitar heurística local)

4) Parâmetros do pipeline

Edite config/pipeline.toml:

```toml
[pipeline]
toc_scan_pages = 8
max_topic_candidates = 3
max_pages_per_metadata = 5

# Paralelismo controlado (fase de extração por metadado)
max_workers = 6
```

## Como rodar

#### Opção A: Via CLI (recomendado)

Após `pip install -e .`:

```bash
gmp \
  --mode acquisition \
  --pdf data/Relatorio_Aquisicao_Streamer.pdf \
  --outdir out \
  --catalog config/metadata_catalog.toml \
  --openai-toml config/openai.toml \
  --pipeline-toml config/pipeline.toml \
  --pipeline-version 0.1.0 \
  --log-level INFO
```

##### Logging (níveis)

O CLI aceita --log-level com os níveis padrão do Python logging:
- DEBUG: mais verboso (útil para diagnóstico e desenvolvimento)
- INFO: padrão (progresso geral do pipeline)
- WARNING: alertas (situações anormais mas recuperáveis)
- ERROR: erros (falhas em alguma etapa; o pipeline pode continuar dependendo do tratamento)
- CRITICAL: falhas graves (normalmente impedem a execução correta)

Exemplo:
```bash
gmp --log-level DEBUG --help
```

#### Opção B: Via runner manual

```bash
python -m tests.manual_run_llm_pipeline
```

## Modo processing (relatórios de processamento sísmico)

Além do modo padrão (acquisition), o projeto suporta o modo processing, voltado para relatórios de processamento.
Nesse modo, a extração é orientada a etapas canônicas do fluxo (metodologia/algoritmo + parâmetros + QCs + evidências), em vez de campos pontuais de aquisição.

#### Catálogo do modo processing

Use o catálogo de etapas em:
- config/processing_catalog.toml

    >Ele segue o mesmo schema do catálogo de aquisição (itens em [[metadata]]), mas cada item representa uma etapa (ex.: Q-compensation, Denoising, Trim statics, Migration/Imaging, Velocity model building, etc.), usando section_hints + patterns para lidar com variabilidade de títulos.

#### Saída do modo processing

##### `processing_summary.toml` (sub-etapas, sem dedupe)

No modo `processing`, o arquivo `out/processing_summary.toml` contém `[[workflow_steps]]` onde **cada entrada é uma sub-etapa** (ex.: `Q-compensation`, `Post Stack Denoising`, `Trim Statics`), extraída a partir das páginas do(s) tópico(s) candidatos.

Observações importantes:

- **Sem dedupe:** a mesma sub-etapa pode aparecer repetida em diferentes tópicos/trechos do relatório. Isso é intencional, pois cada ocorrência pode ter parâmetros e contexto distintos.
- `name` é sempre o nome da **sub-etapa**.
- `parent_step` é obrigatório e corresponde ao item do catálogo (`processing_catalog.toml`) que motivou a busca (ex.: `Preconditioning`).
- `topic_title` e `topic_section_id` preservam o contexto do TOC onde a sub-etapa foi encontrada.
- A ordenação no arquivo segue a **ordem de ocorrência** (por `evidence_page`, com fallback em `page_start`).

Campos principais por `workflow_steps`:
- `name`, `parent_step`
- `topic_title`, `topic_section_id`
- `page_start`, `page_end`
- `methodology`
- `parameters` (dicionário normalizado quando possível, ex.: `Q`, `ref_frequency_hz`, `limit_db`)
- `qcs` (lista de QCs mencionados para aquela sub-etapa)
- `evidence`, `evidence_page`
- `confidence`, `source`

Os arquivos abaixo continuam sendo gerados normalmente:
- out/toc_inferred.toml
- out/run_audit.toml

## Como rodar (processing)

```bash
gmp \
  --mode processing \
  --pdf data/Relatorio_Processamento_3D_IARA_72dpi.pdf \
  --outdir out \
  --catalog config/processing_catalog.toml \
  --openai-toml config/openai.toml \
  --pipeline-toml config/pipeline.toml \
  --pipeline-version 0.1.0 \
  --log-level INFO
```

## Como funciona (visão rápida)

1. Extrai Markdown das primeiras toc_scan_pages páginas e usa o LLM para inferir o TOC.
2. Faz hardening do TOC (ordenação/duplicatas/clamps + warnings).
3. Para cada item do catálogo, escolhe tópicos candidatos com base no TOC (serial).
4. Para cada tópico candidato, carrega até max_pages_per_metadata páginas (lazy + cache).
5. Resolve cada item em paralelo:
    - tenta heurística local (regex/label-value)
    - se necessário, chama o LLM para extração/estruturação
6. Escreve os TOMLs correspondentes ao modo escolhido, além de toc_inferred.toml e run_audit.toml.

## Principais módulos
- src/geophysical_metadata_pipeline/pdf_md.py: extração lazy + cache por página (Markdown)
- src/geophysical_metadata_pipeline/toc_llm.py: extração/inferência do TOC via LLM
- src/geophysical_metadata_pipeline/toc_hardening.py: validações e warnings do TOC
- src/geophysical_metadata_pipeline/topic_match_llm.py: seleção de tópicos candidatos por item do catálogo
- src/geophysical_metadata_pipeline/heuristics.py: extração local (regex/label-value)
- src/geophysical_metadata_pipeline/value_extract_llm.py: extração final via LLM (modo acquisition)
- src/geophysical_metadata_pipeline/processing_extract_llm.py: extração/estruturação por etapa (modo processing)
- src/geophysical_metadata_pipeline/llm_client.py: AzureOpenAI client + parsing JSON + retry/backoff
- src/geophysical_metadata_pipeline/run_pipeline.py: orquestração + logs + paralelismo controlado
- src/geophysical_metadata_pipeline/cli.py: CLI (gmp)

## Exemplo de saída

#### 1) out/extraction_result.toml (exemplo)

```toml
[document]
file_name = "Relatorio_Aquisicao_Streamer.pdf"
pages_total = 200
extraction_timestamp = "2026-09-22T14:03:12-03:00"
pipeline_version = "0.1.0"

[[results]]
name = "Sample Rate"
valor = "2 ms"
evidencia = "Sample Rate : 2 ms"
pagina = 24
topico = "7.2 SYSTEM DETAILS"
confianca = 0.92
is_null = false

[[results]]
name = "Streamer Length"
valor = "4500 m"
evidencia = "Streamer length : 4500 m"
pagina = 26
topico = "7.4 STREAMERS"
confianca = 0.92
is_null = false

[[results]]
name = "Shot Interval"
valor = ""
evidencia = ""
pagina = 0
topico = ""
confianca = 0.0
is_null = true
```

Notas:
- Quando is_null = true, os campos valor/evidencia/pagina/topico/confianca são zerados por padrão.
- O campo confianca é um score heurístico/LLM (não é probabilidade calibrada).

#### 2) out/run_audit.toml (exemplo de trecho)

```toml
[document]
file_name = "Relatorio_Aquisicao_Streamer.pdf"
pages_total = 200

[pipeline]
toc_scan_pages = 8
max_topic_candidates = 3
max_pages_per_metadata = 5
max_workers = 6

[toc_stats]
entries = 42

[toc_hardening]
input_entries = 45
output_entries = 42
dropped_duplicates = 3
dropped_invalid = 0
reordered = true
clamped_pages = 0
fixed_page_end = 0

[[metadata]]
name = "Sample Rate"

[[metadata.topics_tested]]
topico = "7.2 SYSTEM DETAILS"
page_start = 24
page_end = 24
pages_sent = [24]

[metadata.topics_tested.local]
attempted = true
hit = true
score = 0.92

[metadata.topics_tested.llm]
attempted = false
hit = false
score = ""

[metadata.resolved]
name = "Sample Rate"
is_null = false
valor = "2 ms"
pagina = 24
topico = "7.2 SYSTEM DETAILS"
confianca = 0.92
source = "regex"
```

Notas:
- Em metadata.topics_tested, você consegue ver exatamente quais páginas foram enviadas e se resolveu por regex ou por llm.
- Em toc_hardening.warnings (quando existir) ficam sinais de gaps/spans suspeitos no TOC.

## Exemplo de saída (modo `processing`)

### `out/processing_summary.toml` (exemplo)

```toml
[document]
file_name = "Relatorio_Processamento_3D_IARA_72dpi.pdf"
pages_total = 114
extraction_timestamp = "2026-09-25T10:12:55-03:00"
pipeline_version = "0.1.0"

[[workflow_steps]]
name = "Q-compensation"
parent_step = "Preconditioning"
topic_title = "7.3 Q-amplitude compensation"
topic_section_id = "7.3"
page_start = 110
page_end = 110
methodology = "Q-amplitude compensation to reduce attenuation and boost deeper signal while avoiding overcompensation."
parameters = { Q = "120", ref_frequency_hz = "35", limit_db = "12" }
qcs = [
  "Amplitude spectra comparison",
  "Amplitude decay comparison"
]
evidence = "The Q-Compensation was applied consistently ... using Q = 120, reference frequency 35 Hz and a 12 dB limit to avoid overcompensation."
evidence_page = 110
confidence = 0.90
source = "llm"

[[workflow_steps]]
name = "Post Stack Denoising"
parent_step = "Preconditioning"
topic_title = "7.4 Post Stack Denoising"
topic_section_id = "7.4"
page_start = 111
page_end = 111
methodology = "Low-rank filter in the F-x domain to attenuate residual random and incoherent noise while preserving primaries."
parameters = { algorithm = "low-rank filter", domain = "F-x" }
qcs = [
  "Difference section (before/after denoise)",
  "Event continuity / SNR qualitative check"
]
evidence = "We applied a low-rank filter in the F-x domain to attenuate residual random and incoherent noise..."
evidence_page = 111
confidence = 0.88
source = "llm"

[[workflow_steps]]
name = "Trim Statics"
parent_step = "Preconditioning"
topic_title = "7.5 Trim Statics"
topic_section_id = "7.5"
page_start = 112
page_end = 112
methodology = "Alignment of partial stacks using a lowest-order Taylor expansion approach to estimate time-shift differences."
parameters = { method = "lowest-order Taylor expansion", reference = "mid stack full azimuth" }
qcs = [
  "Cross-correlation maps between partial stacks (Near-Mid, Near-Far, Near-Ufar)"
]
evidence = "The applied method is based on a lowest-order Taylor expansion ... using mid stack full azimuth as reference..."
evidence_page = 112
confidence = 0.87
source = "llm"

# Exemplo de repetição (sem dedupe): mesma sub-etapa em outro tópico/recorte
[[workflow_steps]]
name = "Q-compensation"
parent_step = "Migration / Imaging"
topic_title = "LSRTM preconditioning workflow"
topic_section_id = "7.2"
page_start = 106
page_end = 109
methodology = "Q-compensation applied as part of the preconditioning workflow for imaging, focusing on amplitude preservation."
parameters = { Q = "120", ref_frequency_hz = "35", limit_db = "12" }
qcs = [
  "AVA QC",
  "AVAz QC",
  "Well QC (inversion tests around wells)"
]
evidence = "The Q-Amplitude compensation step reduces the attenuation of amplitude and boosts the signal in the deeper parts..."
evidence_page = 109
confidence = 0.84
source = "llm"
```

## Como adicionar um novo metadado

Você adiciona metadados em config/metadata_catalog.toml, dentro do array [[metadata]].

### Passo a passo
1. dicione um novo bloco [[metadata]] com name e, idealmente, description.
2. Preencha section_hints com palavras/expressões que costumam aparecer no TOC ou no texto (isso ajuda a seleção de tópicos e também o fallback “label: value”).
3. (Recomendado) Adicione patterns (regex) para a heurística local resolver sem LLM.
4. Prefira regex com grupo nomeado (?P<value>...).
5. Faça padrões tolerantes a : e = e variações de espaçamento.
6. Se fizer sentido, defina units_expected para evitar falsos positivos (ex.: ["ms"], ["m"], ["Hz"]).
7. Ajuste accept_score (threshold para aceitar extração local).
8. Valores comuns: 0.80–0.92.
9. Se você quer “ser conservador” (evitar falso positivo), use maior.

### Exemplo: adicionando “Near trace offset”

```toml
[[metadata]]
name = "Near trace offset"
description = "Offset do traço mais próximo (near trace offset)"
section_hints = ["Key parameters", "Acquisition parameters", "Streamer layout"]
units_expected = ["m"]
accept_score = 0.85

# Recomenda-se usar (?P<value>...) para capturar o valor.
patterns = [
  '(?im)^\\s*Near\\s*trace\\s*offset\\s*[:=]\\s*(?P<value>\\d+(?:\\.\\d+)?\\s*m)\\s*$'
]
```

## Boas práticas para patterns
- Use flags inline (?im) para multiline + ignorecase.
- Ancora por linha (^...$) quando o PDF tiver formatação consistente.
- Capture somente o valor e unidade em (?P<value>...).
- Se existir tabela, um pattern “linha inteira” ainda pode funcionar, porque o pymupdf4llm normalmente lineariza como texto.

## Troubleshooting
- Erro de certificado / TLS: confirme config/openai.toml e o arquivo ca_petrobras.pem na raiz.
- Instabilidade com max_workers: reduza-o (ex.: 2–4) e/ou aumente max_retries.
- Muitos is_null=true: aumente max_pages_per_metadata, refine section_hints e patterns no catálogo.
