# Contributing to SocialAgent-Team10

Welcome! We're excited to have your contributions. This guide will help you get set up and familiar with our development workflow.

## Development Setup

1. **Python Environment**
   We use Python 3.12. Create and activate a virtual environment:
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Playwright Browsers**
   The ingestion engine uses Playwright for browser automation.
   ```bash
   playwright install chromium
   ```

4. **Setup Ollama**
   We use local LLM inference via Ollama. Make sure it is installed and pull the required models:
   ```bash
   ollama pull llama3.1:8b
   ollama pull llama3.2:1b
   ```

## Running Tests

Our test suite includes both offline tests and live integration tests. To keep CI green and test without network access to Ollama or Nominatim, run the offline test suite:
```bash
pytest -k "not live"
```

## Branch and PR Workflow

1. Check out the `ingestion-engine` branch (or branch off of it).
2. Commit your changes in logically separated commits.
3. Push to your branch and open a Pull Request.
4. Ensure CI tests pass before requesting a review.

## Known Gotchas

### Campus TLS Interception
If you are working from a campus network, you might experience TLS interception which can break `git push` or the Nominatim geocoding API.
* **Workaround 1**: Use a mobile hotspot to bypass the campus network.
* **Workaround 2**: Configure Git to use OpenSSL backend:
  ```bash
  git config --global http.sslBackend openssl
  ```
