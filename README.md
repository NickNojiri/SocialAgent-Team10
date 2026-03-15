# SocialAgent-Team10
oh who wants coffee

social-agent-team10/
├── .env                # Secret API keys (Google Places, etc.)
├── .gitignore          # Tells Git to ignore .env and local DB files
├── requirements.txt    # List of all Python libraries needed
├── README.md           # Instructions for the professor
├── data/               # Folder where ChromaDB stores your "Vector Memory"
│   └── chroma.db       # Actual local database file
├── src/                # All your source code lives here
│   ├── __init__.py     # Makes this folder a Python package
│   ├── main.py         # Entry point: Runs the FastAPI server
│   ├── api/            # API Route definitions
│   │   └── endpoints.py
│   ├── logic/          # The "Brain" (LLM & NLP logic)
│   │   ├── scheduler.py  # Llama 3.1 negotiation logic
│   │   └── parser.py     # spaCy/Duckling date extraction
│   ├── services/       # External tool integrations
│   │   └── google_maps.py # Google Places API calls
│   └── utils/          # Helper functions (logging, date formatting)
└── tests/              # Folder for testing your AI logic