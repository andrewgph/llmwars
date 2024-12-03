import os
import subprocess
import sys
import json
import time
import signal
from dataclasses import dataclass
from typing import List
import threading
import argparse

AGENT_USER = "agent"


@dataclass
class Agent:
    id: int
    name: str
    path: str
    process: subprocess.Popen
    stdout_file: object
    stderr_file: object
    was_stopped: bool = False

def start_agent(agent_id: int, agent_config_file: str) -> Agent:
    # Load config file from AGENT_SPACE directory
    config_path = os.path.join(os.environ["AGENT_SPACE"], agent_config_file)
    print(f"Loading agent config from {config_path}", flush=True)
    with open(config_path, "r") as f:
        agent_config = json.load(f)

    agent_path = os.path.join(os.environ["AGENT_SPACE"], agent_config["agent_path"])
    name = agent_config["name"]
    
    # Create output files in the mounted directory
    stdout_file = open(f'{os.environ["SHARED_LOGS"]}/agent_{name}_{agent_id}_stdout.log', 'w')
    stderr_file = open(f'{os.environ["SHARED_LOGS"]}/agent_{name}_{agent_id}_stderr.log', 'w')
    
    process = subprocess.Popen(
        ["su", "-c", f"python3 -u {agent_path}", AGENT_USER],
        stdout=stdout_file,
        stderr=stderr_file,
        bufsize=1,
        universal_newlines=True,
        preexec_fn=os.setsid,
        env={
            "PYTHONPATH": os.environ["PYTHONPATH"],
            "SHARED_LOGS": os.environ["SHARED_LOGS"],
            "AGENT_SPACE": os.environ["AGENT_SPACE"],
            "AGENT_API_KEY": agent_config.get("api_key", "")
        }
    )
    return Agent(id=agent_id, name=name, path=agent_path, process=process, 
                stdout_file=stdout_file, stderr_file=stderr_file)

def game_loop(agents: List[Agent], timeout_seconds: int):
    start_time = time.time()

    # Monitor processes until any one terminates
    while True:
        for agent in agents:
            if agent.process.poll() is not None:
                print(f"Agent {agent.id} terminated with return code: {agent.process.returncode}", flush=True)
                agent.was_stopped = True

                # Check if other agents are also stopped
                for other_agent in agents:
                    if other_agent.process.poll() is not None:
                        other_agent.was_stopped = True

                return

        if time.time() - start_time > timeout_seconds:
            print("Game timeout reached. Exiting.", flush=True)
            return

def start_services():
    # Start LLM server
    llm_server = subprocess.Popen(
        [sys.executable, "-u", os.environ.get('ROOT_SPACE') + "/llm_server.py"],
        stdout=open(os.environ.get('SHARED_LOGS') + "/llm_server.log", 'w', buffering=1),
        stderr=open(os.environ.get('SHARED_LOGS') + "/llm_server_error.log", 'w', buffering=1),
        universal_newlines=True,
        env={
            "PYTHONPATH": os.environ["PYTHONPATH"],
            "SHARED_LOGS": os.environ["SHARED_LOGS"],
            "ROOT_SPACE": os.environ["ROOT_SPACE"]
        }
    )
    
    # Start file monitor
    file_monitor = subprocess.Popen(
        [sys.executable, "-u", os.environ.get('ROOT_SPACE') + "/file_monitor.py"],
        stdout=open(os.environ.get('SHARED_LOGS') + "/file_monitor.log", 'w', buffering=1),
        stderr=open(os.environ.get('SHARED_LOGS') + "/file_monitor_error.log", 'w', buffering=1),
        universal_newlines=True,
        env={
            "PYTHONPATH": os.environ["PYTHONPATH"],
            "SHARED_LOGS": os.environ["SHARED_LOGS"],
            "AGENT_SPACE": os.environ["AGENT_SPACE"]
        }
    )
    
    return llm_server, file_monitor

def main():
    # Add argument parsing
    parser = argparse.ArgumentParser()
    parser.add_argument('--game-timeout-seconds', type=int, default=60,
                       help='Maximum game duration in seconds')
    parser.add_argument('agent_config_files', nargs='+',
                       help='One or more agent configuration files')
    
    args = parser.parse_args()
    
    # Start support services
    llm_server, file_monitor = start_services()

    # Wait for services to start
    time.sleep(2)
    
    try:
        print(f"game.py Process ID: {os.getpid()}, User ID: {os.getuid()}", flush=True)
        
        # Use agent config files from parsed arguments instead of sys.argv
        if not args.agent_config_files:
            print("Please provide at least one agent file path")
            sys.exit(1)
        
        # Start each agent and keep track of processes
        agents = []
        for idx, agent_config_file in enumerate(args.agent_config_files):
            agent = start_agent(idx, agent_config_file)
            agents.append(agent)

        for agent in agents:
            print(f"Agent at path {agent.path} given ID: {agent.id} and started with PID: {agent.process.pid}")

        # Pass timeout to game_loop
        game_loop(agents, timeout_seconds=args.game_timeout_seconds)

        # Ensure all agents are stopped at the end of the game
        print("Stopping all agents", flush=True)

        # Kill all processes owned by AGENT_USER (as root)
        subprocess.run(["pkill", "-9", "-u", AGENT_USER])
        
        # Add a small delay to allow processes to be terminated
        time.sleep(1)
        
        # Verify that all agents are stopped
        for agent in agents:
            if agent.process.poll() is not None:
                print(f"Agent {agent.id} was stopped", flush=True)
            else:
                print(f"Agent {agent.id} was not stopped", flush=True)
        
        for agent in agents:
            print(f"Agent {agent.id} was stopped: {agent.was_stopped}")
            # Close the output files
            agent.stdout_file.close()
            agent.stderr_file.close()

        # Write the game result to the shared logs
        with open(os.environ.get('SHARED_LOGS') + "/game_result.json", "w") as f:
            json.dump({
                "agents": [{"id": agent.id, "name": agent.name, "was_stopped": agent.was_stopped} for agent in agents]
            }, f)
    finally:
        # Cleanup
        llm_server.terminate()
        file_monitor.terminate()
        
if __name__ == "__main__":
    main()
