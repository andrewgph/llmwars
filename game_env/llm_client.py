import logging
import os
import requests

logger = logging.getLogger(__name__)

class LLMClient:
    def __init__(self, server_url="http://127.0.0.1:5000", api_key=None):
        self.server_url = server_url
        self.api_key = api_key or os.environ.get("AGENT_API_KEY")
        assert self.api_key is not None, "API key must be provided either directly or through AGENT_API_KEY environment variable"
        logger.info(f"Initialized LLMClient with server URL: {server_url}")

    def generate(self, messages):
        """
        Generate a response from the LLM server
        
        Args:
            messages: List of message dictionaries, each containing 'role' and 'content'
                     Example: [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]
        """
        try:
            logger.debug(f"Sending request to LLM server with {len(messages)} messages")
            response = requests.post(
                f"{self.server_url}/generate",
                json={"messages": messages},
                headers={"X-Agent-API-Key": self.api_key}
            )
            response.raise_for_status()
            logger.debug("Successfully received response from LLM server")
            return response.json()["text"]
        except Exception as e:
            logger.error(f"Error generating response: {e}")
            return None