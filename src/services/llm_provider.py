from langchain_ollama import OllamaLLM

class LlamaProvider:
    @staticmethod
    def get_model():
        # Centralized LLM configuration ensures all files use the same model configuration.
        return OllamaLLM(model="llama3.2:latest")
