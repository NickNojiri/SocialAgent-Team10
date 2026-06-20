# RUNBOOK: SocialAgent-Team10

This runbook covers how to operate the various services in the ingestion engine locally.

## Running Services

The system is composed of several FastAPI services running on distinct ports:

- **Ingestion Serving (8000)**: `uvicorn src.ingestion.serving.app:app --port 8000 --reload`
- **LLM Extraction (8001)**: `uvicorn src.ingestion.pipeline.llm_extractor:app --port 8001 --reload`
- **Database (8002)**: `uvicorn src.ingestion.pipeline.db:app --port 8002 --reload`
- **Recommend (8003)**: `uvicorn recommend.main:app --port 8003 --reload` (Phase 6 service)

### Using `.claude/launch.json`
If you are using an IDE with a launch configuration (like VS Code or Claude Code), you can use the provided `.claude/launch.json` to start all these services simultaneously in debug mode.

## Environment Variables

Refer to `.env.example` for the required configuration. Ensure `.env` is populated with:
- `DISCORD_TOKEN`, `BOT_CHANNEL_ID`
- `OLLAMA_URL`, `OLLAMA_MODEL`
- `LLM_URL`, `DB_URL`, `RECOMMEND_URL`
- `CHROMA_PATH`

## Troubleshooting

### Ollama Down
If the Ollama service is unreachable, the pipeline will fall back to using offline heuristics instead of LLM extraction. This allows basic ingestion but might miss complex structured details.

### Nominatim / TLS Issues
If the Nominatim geocoding service is unreachable or blocked (often due to campus TLS interception), the geographic coordinates will remain unresolved. See the `CONTRIBUTING.md` for Git TLS workarounds (using a hotspot or `git config --global http.sslBackend openssl`).

### Instagram Login Wall
When using the Instagram extractor, hitting a login wall is an **expected** failure mode. The system handles this gracefully, but you may need to provide session cookies or run locally with a headed browser to authenticate if required.

### Recommend Service Empty Results
If the recommendation service returns empty results or 404s, it means the vector store hasn't been populated. Run the ingestion pipeline with the `--chroma` flag first to embed and store documents:
```bash
python -m src.ingestion.cli ... --chroma
```
