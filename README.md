# signfinder-core

Core engine for SignFinder — automatic signature placement in contracts.

Ядро SignFinder без UI и привязки к конкретному облаку. Используется в Cloud API
(`signfinder-api`), Streamlit-приложении и Desktop on-prem контейнере (v1.11).

## Installation

Для локальной разработки (Desktop on-prem):

    pip install signfinder-core

Для Google Cloud Platform интеграции:

    pip install signfinder-core[gcs]

Из локального исходника:

    pip install -e ".[gcs,dev]"

## Quick start

    from signfinder import SignFinder

    sf = SignFinder()  # читает env vars

    with open("contract.pdf", "rb") as f:
        pdf_bytes = f.read()

    result = sf.analyze(pdf_bytes, language="ru")
    print(f"Traffic light: {result.traffic_light}")
    print(f"Found {len(result.anchors)} signature places")

    signed = sf.sign(pdf_bytes, result.anchors)
    with open("signed.pdf", "wb") as f:
        f.write(signed)

## Configuration

Env vars — см. `.env.example`:

- `STORAGE_MODE` = `local` | `gcs`
- `STORAGE_PATH` (для local)
- `GCS_BUCKET` (для gcs)
- `ANTHROPIC_API_KEY`
- `LOG_LEVEL`

Можно передать всё явно в конструктор `SignFinder(...)` без env vars.

## Architecture

```
signfinder/
├── config          # Config dataclass + env loading
├── storage         # StorageBackend protocol + Local/GCS backends
├── pdf             # PDF parse/overlay/preview/language
├── anchors         # TextAnchor model + builder + finder
├── fingerprint     # Документный fingerprint (simhash + structure)
├── templates       # DocumentTemplate + matcher + storage CRUD
├── traffic_light   # Светофор green/yellow
├── llm             # LLMClient abstraction + AnthropicClient
├── prompts         # Все LLM промпты в одном месте
├── pipeline        # PipelineAuto1 — оркестратор (resolver + extractor + finder + validator)
└── utils           # Logging, helpers
```

## Development

    pip install -e ".[dev]"
    pytest tests/
    ruff check signfinder/
