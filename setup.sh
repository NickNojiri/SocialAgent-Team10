#!/bin/bash

# 1. Install Ollama if missing
if ! command -v ollama &> /dev/null
then
    echo "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "Ollama already installed."
fi

# 2. Setup Python environment
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt

# 3. Pull the model (Ollama must be running for this, 
# so we run it in the background briefly)
ollama serve & 
sleep 5
ollama pull llama3.2:1b
pkill ollama

echo "✅ Setup Complete! To start, run 'ollama serve' in one tab and 'python3 test_unit.py' in another."