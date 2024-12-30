# Standard library imports
import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import List

# Third party imports
from ebpf_monitor import EbpfMonitor

@dataclass
class Agent:
    id: int
    name: str
    path: str
    process: subprocess.Popen
    stdout_file: object
    stderr_file: object
    was_stopped: bool = False

def generate_api_key():
    return f"agent_key_{uuid.uuid4().hex}"

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

def start_agent(agent_id: int, agent_config_file: str, api_key: str) -> Agent:
    # Load config file from AGENT_SPACE directory
    config_path = os.path.join(os.environ["AGENT_SPACE"], agent_config_file)
    logging.info(f"Loading agent config from {config_path}")
    with open(config_path, "r") as f:
        agent_config = json.load(f)

    original_agent_path = os.path.join(os.environ["AGENT_SPACE"], agent_config["agent_path"])
    
    # Generate random filename and copy the agent file
    random_filename = f"agent_{uuid.uuid4().hex}.py"
    agent_path = os.path.join(os.environ["AGENT_SPACE"], random_filename)
    shutil.copy2(original_agent_path, agent_path)
    
    name = agent_config["name"]
    
    # Create output files in the mounted directory
    stdout_file = open(f'{os.environ["AGENT_LOGS"]}/agent_{name}_{agent_id}_stdout.log', 'w')
    stderr_file = open(f'{os.environ["AGENT_LOGS"]}/agent_{name}_{agent_id}_stderr.log', 'w')
    
    process = subprocess.Popen(
        ["su", "-c", f"/usr/bin/python3 -u {agent_path}", os.environ["AGENT_USER"]],
        stdout=stdout_file,
        stderr=stderr_file,
        bufsize=1,
        universal_newlines=True,
        preexec_fn=os.setsid,
        env={
            "AGENT_LOGS": os.environ["AGENT_LOGS"],
            "AGENT_SPACE": os.environ["AGENT_SPACE"],
            "AGENT_API_KEY": api_key
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
                logging.info(f"Agent {agent.id} terminated with return code: {agent.process.returncode}")
                agent.was_stopped = True

                # Check if other agents are also stopped
                for other_agent in agents:
                    if other_agent.process.poll() is not None:
                        other_agent.was_stopped = True

                return

        if time.time() - start_time > timeout_seconds:
            logging.info("Game timeout reached. Exiting.")
            return

def start_services(api_key_configs, simultaneous_turns):
    # Create temporary config file for LLM server
    temp_config = tempfile.NamedTemporaryFile(mode='w', delete=False)
    json.dump(api_key_configs, temp_config)
    temp_config.close()

    # Start LLM server with path to config
    llm_server = subprocess.Popen(
        [sys.executable, "-u", os.environ.get('ROOT_SPACE') + "/llm_server.py", 
         "--api-key-config", temp_config.name],
        stdout=open(os.environ.get('ROOT_LOGS') + "/llm_server.log", 'w', buffering=1),
        stderr=open(os.environ.get('ROOT_LOGS') + "/llm_server_error.log", 'w', buffering=1),
        universal_newlines=True,
        env={
            "ROOT_LOGS": os.environ["ROOT_LOGS"],
            "ROOT_SPACE": os.environ["ROOT_SPACE"],
            "LLM_SERVER_SIMULTANEOUS_TURNS": str(simultaneous_turns).lower()
        }
    )
    
    # Start file monitor
    file_monitor = subprocess.Popen(
        [sys.executable, "-u", os.environ.get('ROOT_SPACE') + "/file_monitor.py"],
        stdout=open(os.environ.get('ROOT_LOGS') + "/file_monitor.log", 'w', buffering=1),
        stderr=open(os.environ.get('ROOT_LOGS') + "/file_monitor_error.log", 'w', buffering=1),
        universal_newlines=True,
        env={
            "ROOT_LOGS": os.environ["ROOT_LOGS"],
            "AGENT_SPACE": os.environ["AGENT_SPACE"]
        }
    )
    
    return llm_server, file_monitor, temp_config.name

def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('--game-timeout-seconds', type=int, default=60,
                       help='Maximum game duration in seconds')
    parser.add_argument('--simultaneous-turns', action='store_true', default=False,
                       help='Allow simultaneous turns in LLM server')
    parser.add_argument('agent_config_files', nargs='+',
                       help='One or more agent configuration files')
    
    return parser.parse_args()

def main():
    setup_logging()
    process_monitor = EbpfMonitor(os.environ["ROOT_LOGS"])
    process_monitor.start()

    args = parse_arguments()
    
    try:
        logging.info(f"game.py Process ID: {os.getpid()}, User ID: {os.getuid()}")
        
        if not args.agent_config_files:
            logging.error("Please provide at least one agent file path")
            sys.exit(1)
        
        # Generate API keys and configs for each agent
        api_key_configs = {}
        agent_configs = []
        for agent_config_file in args.agent_config_files:
            with open(os.path.join(os.environ["AGENT_SPACE"], agent_config_file)) as f:
                config = json.load(f)
                api_key = generate_api_key()
                config_copy = config.copy()
                config_copy['api_key'] = api_key
                api_key_configs[api_key] = config_copy
                agent_configs.append((agent_config_file, api_key))
        
        # Start services with API key configs
        llm_server, file_monitor, temp_config_path = start_services(api_key_configs, args.simultaneous_turns)

        # Wait for services to start
        time.sleep(2)
        
        # Start each agent with its API key
        agents = []
        for idx, (agent_config_file, api_key) in enumerate(agent_configs):
            agent = start_agent(idx, agent_config_file, api_key)
            agents.append(agent)

        for agent in agents:
            logging.info(f"Agent at path {agent.path} given ID: {agent.id} and started with PID: {agent.process.pid}")

        # Pass timeout to game_loop
        game_loop(agents, timeout_seconds=args.game_timeout_seconds)

        # Ensure all agents are stopped at the end of the game
        logging.info("Stopping all agents")

        # Kill all processes owned by AGENT_USER (as root)
        subprocess.run(["pkill", "-9", "-u", os.environ["AGENT_USER"]])
        
        # Add a small delay to allow processes to be terminated
        time.sleep(1)
        
        # Verify that all agents are stopped
        for agent in agents:
            if agent.process.poll() is not None:
                logging.info(f"Agent {agent.id} was stopped")
            else:
                logging.warning(f"Agent {agent.id} was not stopped")
        
        for agent in agents:
            logging.info(f"Agent {agent.id} was stopped: {agent.was_stopped}")
            # Close the output files
            agent.stdout_file.close()
            agent.stderr_file.close()

        # Write the game result to the root logs
        with open(os.environ.get('ROOT_LOGS') + "/game_result.json", "w") as f:
            json.dump({
                "agents": [{"id": agent.id, "name": agent.name, "was_stopped": agent.was_stopped} for agent in agents]
            }, f)
            f.flush()
            os.fsync(f.fileno())
    finally:
        # Cleanup
        llm_server.terminate()
        file_monitor.terminate()
        process_monitor.stop()
        # Remove temporary config file
        os.unlink(temp_config_path)
        
if __name__ == "__main__":
    main()
