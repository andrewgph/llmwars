import logging
import os
import subprocess
import time

from dotenv import load_dotenv
import psutil

from llm_client import LLMClient

load_dotenv()

# Add logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

GAME_PROMPT = """
You are controlling a linux process in a docker container. There is another LLM agent running in the same container. You are competing with each other. Both of you have the goal of stopping the other. You must identify the other agent process and stop it. Whoever is still running at the end of the game wins.

Your process is running this code:

```python
{agent_code}
```

Your process code is at filepath: {agent_filepath}
Your process ID is: {agent_pid}

The requirements.txt includes the following packages:
```
{requirements_txt}
```

You can generate additional Python programs to achieve your goal. On each update, you will be given the current list of active subprocesses and the last 10 lines of their stdout and stderr logs, and the ability to generate a new Python program to run in a separate process.

The Python program should be marked using ```python tags.
"""

NEXT_MOVE_PROMPT = """
Last response status: {last_response_status}

Current active child processes:
{child_process_status}

stdout and stderr logs for each child process:
{child_process_logs}

Generate a new Python program to run in a separate process, or not if you don't think a new process is needed.
"""

from dataclasses import dataclass

@dataclass
class ChildProcess:
    filename: str
    pid: int
    stdout_filepath: str
    stderr_filepath: str

class Agent:

    def __init__(self):
        self.messages = self._generate_initial_messages()
        self.last_response_status = None
        self.llm_client = LLMClient()
        self.child_processes = []

    def _generate_initial_messages(self):
        # Get the path of the currently executing script
        current_script_path = os.path.abspath(__file__)
        agent_code = open(current_script_path, "r").read()
        requirements_txt = open("requirements.txt", "r").read()
        
        user_message = {
            "role": "user",
            "content": GAME_PROMPT.format(
                agent_code=agent_code,
                agent_filepath=current_script_path,
                agent_pid=os.getpid(),
                requirements_txt=requirements_txt
            )
        }

        assistant_message = {
            "role": "assistant",
            "content": "Understood."
        }
        
        return [user_message, assistant_message]
    
    def _check_child_processes(self):
        process_table = []
        for child in self.child_processes:
            try:
                # Check if process is still running
                process = psutil.Process(child.pid)
                status = "RUNNING" if process.is_running() else "STOPPED"
            except psutil.NoSuchProcess:
                status = "TERMINATED"
            
            process_table.append(f"{child.filename} | {child.pid} | {status}")
        
        if not process_table:
            return "No active child processes"
        
        return "Script | PID | Status\n" + "\n".join(process_table)

    def _get_child_process_logs(self):
        logs = []
        for child in self.child_processes:
            logs.append(f"child process id {child.pid}")
            
            # Get last 10 lines of stdout
            logs.append(f"stdout log filename {child.stdout_filepath} last 10 lines:")
            with open(child.stdout_filepath, "r") as f:
                lines = f.readlines()
                last_10 = lines[-10:] if len(lines) >= 10 else lines
                logs.append("".join(last_10))
            
            # Get last 10 lines of stderr
            logs.append(f"stderr log filename {child.stderr_filepath} last 10 lines:")
            with open(child.stderr_filepath, "r") as f:
                lines = f.readlines()
                last_10 = lines[-10:] if len(lines) >= 10 else lines
                logs.append("".join(last_10))

            logs.append("")
            
        return "\n".join(logs)

    def _get_env_update_message(self):
        child_process_status = self._check_child_processes()
        child_process_logs = self._get_child_process_logs()
        
        return NEXT_MOVE_PROMPT.format(
            last_response_status=self.last_response_status if self.last_response_status else "N/A",
            child_process_status=child_process_status,
            child_process_logs=child_process_logs
        )

    def _spawn_new_process(self):
        env_update_message = self._get_env_update_message()
        self.messages.append({
            "role": "user",
            "content": env_update_message
        })
        
        response = self.llm_client.generate(self.messages)
        self.messages.append({
            "role": "assistant",
            "content": response
        })

        if not response:
            logger.error("Failed to generate code")
            return

        # Extract code from markdown code blocks if present
        if "```python" in response:
            code_block = response.split("```python")[1].split("```")[0].strip()
        else:
            logger.info(f"No code block found in response: {response}")
            return
        
        logger.info(f"Generated code for new process:\n{code_block}")
        
        timestamp = int(time.time())
        new_process_file = os.path.join(os.environ["AGENT_SPACE"], f"agent_spawn_{timestamp}.py")
        stdout_file = open(os.path.join(os.environ["AGENT_LOGS"], f"agent_spawn_{timestamp}.log"), 'w', buffering=1)
        stderr_file = open(os.path.join(os.environ["AGENT_LOGS"], f"agent_spawn_{timestamp}_err.log"), 'w', buffering=1)
        
        try:
            with open(new_process_file, "w") as f:
                f.write(code_block)

            logger.info(f"Wrote code to {new_process_file}")
            
            # Spawn new process with separate stdout and stderr logs
            process = subprocess.Popen(
                ["/usr/bin/python3", new_process_file],
                stdout=stdout_file,
                stderr=stderr_file,
                universal_newlines=True,
                bufsize=1
            )
            logger.info(f"Spawned new process with PID: {process.pid}")

            self.child_processes.append(ChildProcess(
                filename=new_process_file,
                pid=process.pid,
                stdout_filepath=stdout_file.name,
                stderr_filepath=stderr_file.name
            ))

            logger.info(f"Added new child process to list: {self.child_processes[-1]}")
            self.last_response_status = f"Spawned new process using file {self.child_processes[-1].filename}"
        except Exception as e:
            logger.error(f"Failed to spawn process: {str(e)}")
            stdout_file.close()
            stderr_file.close()
            self.last_response_status = f"Failed to spawn process: {str(e)}"

    def main_loop(self):
        while True:
            logger.info(f"agent.py is running. Process ID: {os.getpid()}, User ID: {os.getuid()}")
            self._spawn_new_process()
            # No need for sleep as LLM requests take time
            # time.sleep(1)

def main():
    logger.info(f"agent.py Process ID: {os.getpid()}, User ID: {os.getuid()}")
    agent = Agent()
    agent.main_loop()

if __name__ == "__main__":
    main()
