import requests
import os

class LLMClient:
    def __init__(self, server_url="http://127.0.0.1:5000", api_key=None):
        self.server_url = server_url
        self.api_key = api_key or os.environ.get("AGENT_API_KEY")
        assert self.api_key is not None, "API key must be provided either directly or through AGENT_API_KEY environment variable"

    def generate(self, prompt):
        try:
            response = requests.post(
                f"{self.server_url}/generate",
                json={"prompt": prompt},
                headers={"X-Agent-API-Key": self.api_key}
            )
            response.raise_for_status()
            return response.json()["text"]
        except Exception as e:
            print(f"Error generating response: {e}")
            return None