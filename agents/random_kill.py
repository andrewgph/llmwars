import os
import random
import subprocess
import time
import logging

def main():
    # Set up logging configuration
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    logging.info(f"random_kill.py Process ID: {os.getpid()}, User ID: {os.getuid()}")

    current_user = os.getuid()
    user_processes = []

    # Use 'ps' command to get process information
    ps_output = subprocess.check_output(['ps', '-eo', 'pid,comm,uid'], universal_newlines=True)
    
    for line in ps_output.strip().split('\n')[1:]:  # Skip the header
        pid, comm, uid = line.split(None, 2)
        if int(uid) == current_user:
            user_processes.append(f"PID: {pid}, Name: {comm}")

    logging.info("Processes running as the current user:")
    for proc in user_processes:
        logging.info(proc)

    # Kill a random process
    random_process = random.choice(user_processes)
    pid = random_process.split(":")[1].split(",")[0].strip()  # Extract just the number between "PID:" and ","
    logging.info(f"Killing random process: {random_process}")
    subprocess.run(['kill', '-9', pid])

    # Wait indefinitely
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
