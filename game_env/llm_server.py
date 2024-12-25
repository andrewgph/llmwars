import os
from flask import Flask, request, jsonify
from anthropic import Anthropic
from dotenv import load_dotenv
import json
from datetime import datetime
from openai import OpenAI
import google.generativeai as genai
import argparse

load_dotenv()

app = Flask(__name__)

# Setup LLM clients
claude_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# Store agent configurations
agent_configs = {}

# Replace @app.before_first_request with a flag and before_request
_configs_loaded = False

def load_agent_configs(config_path):
    global agent_configs
    with open(config_path) as f:
        agent_configs = json.load(f)
    print(f"Loaded {len(agent_configs)} agent configurations")

def generate_claude_response(messages, model_name):
    response = claude_client.messages.create(
        max_tokens=8192,
        messages=messages,
        model=model_name,
    )
    return response.content[0].text

def generate_openai_response(messages, model_name):
    response = openai_client.chat.completions.create(
        model=model_name,
        messages=messages,
    )
    return response.choices[0].message.content

def generate_gemini_response(messages, model_name):
    model = genai.GenerativeModel(model_name=model_name)
    # Convert OpenAI-style messages to Gemini format
    gemini_messages = [
        {
            "role": msg["role"],
            "parts": [msg["content"]]
        }
        for msg in messages
    ]
    response = model.generate_content(gemini_messages)
    return response.text

@app.before_request
def setup():
    global _configs_loaded
    if not _configs_loaded:
        # Note: We don't need to load configs here anymore since
        # they're loaded when the server starts
        _configs_loaded = True

@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    messages = data.get('messages', [])
    api_key = request.headers.get('X-Agent-API-Key')

    if not api_key or api_key not in agent_configs:
        return jsonify({"error": "Invalid or missing API key"}), 401

    if not messages:
        return jsonify({"error": "No messages provided"}), 400

    agent_config = agent_configs[api_key]
    print(f"Generating response for agent: {agent_config['name']}, using model: {agent_config['model']}")
    
    try:
        if agent_config['provider'] == 'anthropic':
            response_text = generate_claude_response(messages, agent_config['model'])
        elif agent_config['provider'] == 'openai':
            response_text = generate_openai_response(messages, agent_config['model'])
        elif agent_config['provider'] == 'gemini':
            response_text = generate_gemini_response(messages, agent_config['model'])
        else:
            return jsonify({"error": "Invalid provider"}), 400

        # Log the messages and response
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "agent_name": agent_config['name'],
            "messages": messages,
            "response": response_text
        }
        
        with open(os.path.join(os.environ.get('SHARED_LOGS'), 'llm_interactions.jsonl'), 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
            
        return jsonify({"text": response_text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key-config', required=True,
                       help='Path to JSON file containing API key configurations')
    args = parser.parse_args()
    
    # Load API key configs from the provided file
    load_agent_configs(args.api_key_config)
    
    app.run(host='0.0.0.0', port=5000)