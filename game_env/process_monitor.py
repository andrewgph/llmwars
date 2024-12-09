import subprocess
import threading
import json
import os
import re
from datetime import datetime

class ProcessMonitor:
    def __init__(self, log_dir):
        self.log_dir = log_dir
        self.kill_log_path = os.path.join(log_dir, 'process_kills.json')
        self.kills = []
        self.monitor_thread = None
        self.running = False
        
        # Initialize empty kill log
        self._save_kills()

    def _save_kills(self):
        with open(self.kill_log_path, 'w') as f:
            json.dump(self.kills, f, indent=2)

    def _monitor_dmesg(self):
        # Clear existing dmesg buffer
        subprocess.run(['dmesg', '-c'], check=True)
        
        # Start monitoring dmesg output
        process = subprocess.Popen(
            ['dmesg', '-w'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )

        while self.running:
            line = process.stdout.readline()
            if not line:
                break
                
            # Look for kill signals in dmesg output
            # Example format: "Kill process 1234 (python3) sent by process 5678 (python3)"
            kill_match = re.search(r'Kill process (\d+) \((.*?)\) sent by (\d+)', line)
            if kill_match:
                victim_pid = int(kill_match.group(1))
                victim_name = kill_match.group(2)
                killer_pid = int(kill_match.group(3))
                
                kill_event = {
                    'timestamp': datetime.now().isoformat(),
                    'victim_pid': victim_pid,
                    'victim_name': victim_name,
                    'killer_pid': killer_pid,
                    'dmesg_line': line.strip()
                }
                
                self.kills.append(kill_event)
                self._save_kills()

        process.terminate()

    def start(self):
        """Start monitoring process kills"""
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_dmesg)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()

    def stop(self):
        """Stop monitoring process kills"""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join()

    def get_kills(self):
        """Return list of recorded kill events"""
        return self.kills

    def get_killer_for_victim(self, victim_pid):
        """Find the process that killed the specified victim PID"""
        for kill in reversed(self.kills):
            if kill['victim_pid'] == victim_pid:
                return kill['killer_pid']
        return None 