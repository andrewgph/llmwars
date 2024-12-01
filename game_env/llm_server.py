import os
from flask import Flask, request, jsonify
from anthropic import Anthropic
from dotenv import load_dotenv
import json
from datetime import datetime

load_dotenv()

app = Flask(__name__)
claude_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# Store agent configurations
agent_configs = {}

def load_agent_configs():
    config_dir = os.path.join(os.environ.get('ROOT_SPACE'), 'agent_configs')
    for filename in os.listdir(config_dir):
        if filename.endswith('.json'):
            with open(os.path.join(config_dir, filename)) as f:
                config = json.load(f)
                if 'api_key' in config:
                    agent_configs[config['api_key']] = config
                else:
                    print(f"Skipping {filename} because it does not contain 'api_key'")

# Replace @app.before_first_request with a flag and before_request
_configs_loaded = False

@app.before_request
def setup():
    global _configs_loaded
    if not _configs_loaded:
        load_agent_configs()
        _configs_loaded = True

@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    prompt = data.get('prompt')
    api_key = request.headers.get('X-Agent-API-Key')

    if not api_key or api_key not in agent_configs:
        return jsonify({"error": "Invalid or missing API key"}), 401

    agent_config = agent_configs[api_key]
    print(f"Generating response for agent: {agent_config['name']}, using model: {agent_config['model']}")
    
    try:
        response = claude_client.messages.create(
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
            model=agent_config['model'],
        )
        response_text = response.content[0].text

        # Log the prompt and response
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "agent_name": agent_config['name'],
            "prompt": prompt,
            "response": response_text
        }
        
        with open(os.path.join(os.environ.get('SHARED_LOGS'), 'llm_interactions.jsonl'), 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
            
        return jsonify({"text": response_text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)