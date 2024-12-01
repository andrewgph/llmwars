import requests

class LLMClient:
    def __init__(self, server_url="http://127.0.0.1:5000"):
        self.server_url = server_url

    def generate(self, prompt):
        try:
            response = requests.post(
                f"{self.server_url}/generate",
                json={"prompt": prompt}
            )
            response.raise_for_status()
            return response.json()["text"]
        except Exception as e:
            print(f"Error generating response: {e}")
            return None