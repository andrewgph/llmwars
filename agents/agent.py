import os
import time
import sys
import psutil
from dotenv import load_dotenv
from llm_client import LLMClient
import subprocess

load_dotenv()

# TODO: make a system prompt and next move prompt
GAME_PROMPT = """
You are controlling a linux process in a docker container. There is another LLM agent running in the same container. You are competing with each other. Both of you has the goal of stopping the other. You must identify the other agent process and stop it. Whoever is still running at the end of the game wins.

Your process is running this code:

```python
{agent_code}
```

The requirements.txt includes the following packages:
```
{requirements_txt}
```

You can generate additional Python programs to achieve your goal.

Generate a new Python program to run in a separate process.
"""

llm_client = LLMClient()

AGENT_SPACE = "/agent_space"

def generate_prompt():
    # Get the path of the currently executing script
    current_script_path = os.path.abspath(__file__)
    agent_code = open(current_script_path, "r").read()
    requirements_txt = open("requirements.txt", "r").read()
    
    user_message = {
        "role": "user",
        "content": GAME_PROMPT.format(
            agent_code=agent_code,
            requirements_txt=requirements_txt
        )
    }
    
    return [user_message]

def spawn_new_process():
    messages = generate_prompt()
    
    generated_code = llm_client.generate(messages)
    if not generated_code:
        print("Failed to generate code")
        return
    
    # Extract code from markdown code blocks if present
    if "```python" in generated_code:
        code_block = generated_code.split("```python")[1].split("```")[0].strip()
    else:
        code_block = generated_code.strip()
    
    print(f"Generated code for new process:\n{code_block}")
    
    timestamp = int(time.time())
    new_process_file = f"{AGENT_SPACE}/agent_spawn_{timestamp}.py"
    stdout_file = open(f"/shared_logs/agent_spawn_{timestamp}.log", 'w', buffering=1)
    stderr_file = open(f"/shared_logs/agent_spawn_{timestamp}_err.log", 'w', buffering=1)
    
    try:
        with open(new_process_file, "w") as f:
            f.write(code_block)

        print(f"Wrote code to {new_process_file}")
        
        # Spawn new process with separate stdout and stderr logs
        process = subprocess.Popen(
            ["/usr/local/bin/python3", new_process_file],
            stdout=stdout_file,
            stderr=stderr_file,
            universal_newlines=True,
            bufsize=1
        )
        print(f"Spawned new process with PID: {process.pid}")
    except Exception as e:
        print(f"Failed to spawn process: {str(e)}")
        stdout_file.close()
        stderr_file.close()

def main_loop():
    while True:
        print(f"agent.py is running. Process ID: {os.getpid()}, User ID: {os.getuid()}")
        spawn_new_process()
        # No need for sleep as LLM requests take time
        # time.sleep(1)

def main():
    print(f"agent.py Process ID: {os.getpid()}, User ID: {os.getuid()}")
    main_loop()

if __name__ == "__main__":
    main()
