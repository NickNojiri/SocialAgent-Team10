# This tells the computer to run these shortcuts
setup:
	curl -fsSL https://ollama.com/install.sh | sh
	python3 -m venv .venv
	. .venv/bin/activate && pip install --upgrade pip
	. .venv/bin/activate && pip install -r requirements.txt
	ollama pull llama3.2:1b

test:
	. .venv/bin/activate && python3 test_unit.py

serve:
	ollama serve