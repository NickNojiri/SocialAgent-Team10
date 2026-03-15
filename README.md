# Team 10: AI Social Coordinator Agent
An autonomous agent built with the **Lean Stack** to negotiate meeting times and venues between friends using Llama 3.1 and real-world location data.

## 🚀 Quick Start
To run the full integration test of the agent:

1. **Activate Environment:**
   ```bash
   source .venv/bin/activate



SocialAgent-Team10/
├── .env                # Private API Keys (Google/Ollama)
├── .gitignore          # Keeps the repo clean (ignores .venv, data/, etc.)
├── README.md           # Project documentation
├── requirements.txt    # Project dependencies
├── test_unit.py        # Integration test script (The "Main Entry")
├── .venv/              # Local Python environment (10k+ files, ignored)
├── data/               # Persistent ChromaDB storage
└── src/                # Core Source Code
    ├── main.py         # FastAPI application entry
    ├── api/            # API Route definitions
    ├── logic/          # The "Brain" (Llama 3.1, NLP Parser, Database)
    │   ├── parser.py
    │   ├── scheduler.py
    │   └── database.py
    └── services/       # External Integrations (Google Places API)
        └── google_maps.py

🛠️ The Tech Stack

    LLM: Llama 3.1 (via Ollama)

    Orchestration: LangChain

    API: FastAPI

    Database: ChromaDB (Vector Storage)

    NLP: spaCy & Duckling

📂 Key Components

    NLP Parser (parser.py): Converts messy text like "7ish on Friday" into machine timestamps.

    Venue Scout (Maps.py): Finds the best-rated spots at a midpoint.

    Scheduler (scheduler.py): The LLM "Brain" that resolves conflicts and picks the plan.