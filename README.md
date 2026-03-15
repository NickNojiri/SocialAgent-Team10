Team 10: AI Social Coordinator Agent

An autonomous social agent built with a Lean Stack to negotiate meeting times and venues between friends using local LLMs and real-world location data. This project prioritizes privacy and cost-efficiency by running models and location services locally or via free open-source APIs.
🚀 Quick Start

To get the environment and agent running in a fresh Codespace:

    Automated Setup:
    Bash

    make setup

    Start the AI Engine (Tab 1):
    Bash

    make serve

    Run the Integration Test (Tab 2):
    Bash

    make test

📂 Project Structure
Plaintext

SocialAgent-Team10/
├── .devcontainer/      # Codespace environment configuration
├── src/                # Core Source Code
│   ├── logic/          # The "Brain"
│   │   ├── parser.py       # Nicholas: NLP Time Parsing
│   │   ├── scheduler.py    # Alfredo: LLM Conflict Resolution
│   │   └── database.py     # Persona & Venue Storage
│   └── services/       # External Integrations
│       ├── location_service.py # John: Free OSM/Overpass Integration
│       └── google_maps.py      # (Optional) Premium Google Tier
├── test_unit.py        # End-to-End Integration Test Controller
├── Makefile            # Pro-tier developer shortcuts
├── requirements.txt    # Project dependencies
└── .gitignore          # Prevents .venv, secrets, and cache from being tracked

🛠️ The Tech Stack

    LLM: Llama 3.2 1B (via Ollama) – Lightweight local reasoning.

    Orchestration: LangChain – Manages the flow between tools and the LLM.

    Maps: OpenStreetMap (Nominatim & Overpass API) – Free geocoding and venue discovery.

    NLP: dateparser with custom fallback logic – Robust natural language time extraction.

    Database: ChromaDB – Vector storage for long-term user preferences (Persona memory).

    API: FastAPI (Future Implementation) – For web-based user interaction.

🧠 Key Components
NLP Parser (parser.py)

Converts messy user input like "this Friday at 7pm" into machine-readable timestamps. It includes a fallback mechanism to ensure the LLM receives context even if specific date parsing fails.
Venue Scout (location_service.py)

Calculates the geographic midpoint between participants and queries the OpenStreetMap database for high-rated venues within a reachable radius.
Scheduler (scheduler.py)

The "Agentic" heart of the project. It performs Weighted Negotiation:

    Spatial Awareness: Ensures travel fairness based on midpoint data.

    Persona Alignment: Checks venue metadata against user constraints (e.g., matching a "quiet bar" for a user who hates loud music).

    Reasoning: Provides a structured meeting plan with a clear explanation of why specific venues were chosen.

⌨️ Developer Shortcuts
Command	Action
make setup	Installs Ollama, Python libraries, and pulls the AI model.
make serve	Starts the local LLM server.
make test	Executes the full integration test (test_unit.py).
🔒 Team 10 Master Ignore

We maintain a strict .gitignore to protect privacy and repository health:

    .env (API Keys)

    __pycache__/ (Python temp files)

    .venv/ (Local environment files)

    data/ (Local database storage)