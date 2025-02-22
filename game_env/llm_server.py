import argparse
import asyncio
import json
import logging
import os
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime

from anthropic import Anthropic
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from openai import OpenAI
from google import genai
from google.genai import types

load_dotenv()

app = Flask(__name__)

# Setup LLM clients
claude_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
openrouter_client = OpenAI(
  base_url="https://openrouter.ai/api/v1",
  api_key=os.environ.get("OPENROUTER_API_KEY"),
)
hyperbolic_client = OpenAI(
  base_url="https://api.hyperbolic.xyz/v1",
  api_key=os.environ.get("HYPERBOLIC_API_KEY"),
)
fireworks_client = OpenAI(
  base_url="https://api.fireworks.ai/inference/v1",
  api_key=os.environ.get("FIREWORKS_API_KEY"),
)
gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# Store agent configurations
agent_configs = {}

# Replace @app.before_first_request with a flag and before_request
_configs_loaded = False

# Add after existing global variables
SIMULTANEOUS_TURNS = os.environ.get("LLM_SERVER_SIMULTANEOUS_TURNS", "false").lower() == "true"
turn_map = defaultdict(int)  # Initialize with 0 for any new key
turn_count = 0
turn_lock = threading.Lock()

# Add after other global variables
RESPONSE_POLL_INTERVAL = 0.1  # seconds
RESPONSE_TIMEOUT = 60  # seconds

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)

logger = setup_logging()

def load_agent_configs(config_path):
    global agent_configs
    with open(config_path) as f:
        loaded_configs = json.load(f)
    logger.info(f"Loaded {len(loaded_configs)} agent configurations")

    # Initialize turn map
    for api_key, config in loaded_configs.items():
        if not 'provider' in config:
            logger.info(f"Skipping agent {api_key} because it has no provider")
            continue
        agent_configs[api_key] = config
    logger.info(f"Loaded {len(agent_configs)} agent configurations")

def generate_claude_response(messages, model_name):
    response = claude_client.messages.create(
        max_tokens=8192,
        messages=messages,
        model=model_name,
    )
    return response.content[0].text

def generate_openai_response(messages, model_name):
    # Assumes using a reasoning model
    response = openai_client.chat.completions.create(
        model=model_name,
        reasoning_effort="high",
        messages=messages,
    )
    return response.choices[0].message.content

def generate_openrouter_response(messages, model_name):
    response = openrouter_client.chat.completions.create(
        model=model_name,
        messages=messages,
    )
    return response.choices[0].message.content

def generate_hyperbolic_response(messages, model_name):
    response = hyperbolic_client.chat.completions.create(
        model=model_name,
        messages=messages,
    )
    return response.choices[0].message.content

def generate_fireworks_response(messages, model_name):
    response = fireworks_client.chat.completions.create(
        model=model_name,
        messages=messages,
    )
    return response.choices[0].message.content

def generate_gemini_response(messages, model_name):    
    # Convert OpenAI-style messages to Gemini format
    gemini_messages = [
        {
            "role": "model" if msg["role"] == "assistant" else "user",
            "parts": [{
                "text": msg["content"]
            }]
        }
        for msg in messages
    ]

    # Assumes using a thinking model
    chat = gemini_client.chats.create(
        model=model_name,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(include_thoughts=True),
            http_options=types.HttpOptions(api_version='v1alpha'),
        ),
        history=gemini_messages[:-1]
    )

    response = chat.send_message(gemini_messages[-1]["parts"][0]["text"])

    return response.text

def initialize_turn_map():
    for api_key, config in agent_configs.items():
        if config['provider'] == 'anthropic':
            turn_map[api_key] = 0
        elif config['provider'] == 'openai':
            turn_map[api_key] = 0
        elif config['provider'] == 'gemini':
            turn_map[api_key] = 0
        elif config['provider'] == 'openrouter':
            turn_map[api_key] = 0
        elif config['provider'] == 'hyperbolic':
            turn_map[api_key] = 0
        elif config['provider'] == 'fireworks':
            turn_map[api_key] = 0
        else:
            logger.error(f"Invalid provider specified: {config['provider']}")

def mark_turn_complete(api_key):
    with turn_lock:
        turn_map[api_key] += 1

def undo_turn(api_key):
    with turn_lock:
        turn_map[api_key] -= 1

async def wait_for_all_responses(api_key):
    global turn_count
    start_time = time.time()
    while time.time() - start_time < RESPONSE_TIMEOUT:
        with turn_lock:
            if min(turn_map.values()) == turn_count + 1:
                # All agents complete advance the turn
                turn_count += 1
                return True
            if turn_map[api_key] == turn_count:
                # This agent is now unblocked
                return True
        await asyncio.sleep(RESPONSE_POLL_INTERVAL)
    return False

@app.before_request
def setup():
    global _configs_loaded
    if not _configs_loaded:
        # Note: We don't need to load configs here anymore since
        # they're loaded when the server starts
        _configs_loaded = True

@app.route('/generate', methods=['POST'])
async def generate():
    data = request.json
    messages = data.get('messages', [])
    api_key = request.headers.get('X-Agent-API-Key')

    if not api_key or api_key not in agent_configs:
        logger.warning(f"Invalid API key attempt: {api_key}")
        return jsonify({"error": "Invalid or missing API key"}), 401

    if not messages:
        logger.warning("Request received with no messages")
        return jsonify({"error": "No messages provided"}), 400

    agent_config = agent_configs[api_key]
    logger.info(f"Generating response for agent: {agent_config['name']}, using model: {agent_config['model']}")
    
    try:
        # Log the request details before processing
        logger.info(f"Request details for {agent_config['name']}:")
        logger.info(f"API key: {api_key}")
        logger.info(f"Provider: {agent_config['provider']}")
        logger.info(f"Model: {agent_config['model']}")
        logger.info(f"Messages: {json.dumps(messages, indent=2)}")
        
        if agent_config['provider'] == 'anthropic':
            response_text = generate_claude_response(messages, agent_config['model'])
        elif agent_config['provider'] == 'openai':
            response_text = generate_openai_response(messages, agent_config['model'])
        elif agent_config['provider'] == 'gemini':
            response_text = generate_gemini_response(messages, agent_config['model'])
        elif agent_config['provider'] == 'openrouter':
            response_text = generate_openrouter_response(messages, agent_config['model'])
        elif agent_config['provider'] == 'hyperbolic':
            response_text = generate_hyperbolic_response(messages, agent_config['model'])
        elif agent_config['provider'] == 'fireworks':
            response_text = generate_fireworks_response(messages, agent_config['model'])
        else:
            logger.error(f"Invalid provider specified: {agent_config['provider']}")
            return jsonify({"error": "Invalid provider"}), 400

        # Log the messages and response
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "agent_name": agent_config['name'],
            "api_key": api_key,
            "messages": messages,
            "response": response_text
        }
        
        with open(os.path.join(os.environ.get('ROOT_LOGS'), 'llm_interactions.jsonl'), 'a') as f:
            f.write(json.dumps(log_entry) + '\n')

        # Handle simultaneous turns if enabled
        if SIMULTANEOUS_TURNS:
            mark_turn_complete(api_key)
            logger.debug(f"Marked turn complete for agent {agent_config['name']}")
            
            if await wait_for_all_responses(api_key):
                logger.info(f"All agents responded for turn, returning response for {agent_config['name']}")
                return jsonify({"text": response_text})
            else:
                logger.warning(f"Timeout waiting for other agents' responses")
                undo_turn(api_key)
                return jsonify({
                    "error": "Timeout waiting for other agents' responses"
                }), 408
        else:
            # Normal single-response mode
            return jsonify({"text": response_text})
    except Exception as e:
        logger.error(f"Error generating response for {agent_config['name']} with {agent_config['provider']}: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/turn_count', methods=['GET'])
def get_turn_count():
    return jsonify({"turn_count": turn_count})

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key-config', required=True,
                       help='Path to JSON file containing API key configurations')
    args = parser.parse_args()
    
    # Load API key configs from the provided file
    load_agent_configs(args.api_key_config)
    initialize_turn_map()
    
    logger.info("Starting LLM server on port 5000")
    app.run(host='0.0.0.0', port=5000)