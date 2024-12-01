import os
from flask import Flask, request, jsonify
from anthropic import Anthropic
from dotenv import load_dotenv
import json
from datetime import datetime

load_dotenv()

app = Flask(__name__)
claude_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    prompt = data.get('prompt')

    print(f"Received prompt: {prompt}")
    
    try:
        response = claude_client.messages.create(
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
            model="claude-3-5-sonnet-20241022",
        )
        response_text = response.content[0].text

        print(f"Generated response: {response_text}")

        # Log the prompt and response
        log_entry = {
            "timestamp": datetime.now().isoformat(),
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