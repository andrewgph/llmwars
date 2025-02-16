import json
import logging
import os
import threading
import time
from datetime import datetime

from bcc import BPF

class EbpfMonitor:
    def __init__(self, log_dir, uids=None):
        self.log_dir = log_dir
        self.events_log_path = os.path.join(log_dir, 'process_events.json')
        self.events = []
        self.bpf = None
        self.running = False
        self.monitor_thread = None
        self.uids = uids

        self.logger = logging.getLogger(__name__)

        self._save_events()
    
    def _save_events(self):
        with open(self.events_log_path, 'w') as f:
            json.dump(self.events, f, indent=2)
    
    def _process_event(self, cpu, data, size):
        event = self.bpf["events"].event(data)
        
        # Convert bytes to integer before using chr()
        event_type = event.type
        if isinstance(event_type, bytes):
            event_type = int.from_bytes(event_type, byteorder='little')
        
        # Skip events if uid filtering is enabled and uid doesn't match
        if self.uids is not None and event.uid not in self.uids:
            return
            
        event_data = {
            'timestamp': datetime.now().isoformat(),
            'type': chr(event_type),
            'pid': event.pid,
            'ppid': event.ppid,
            'uid': event.uid,
            'comm': event.comm.decode('utf-8', 'replace'),
        }
        
        if event_type == ord('K'):
            event_data['kill_pid'] = event.kill_pid
        
        self.events.append(event_data)
        self._save_events()
    
    def _load_bpf_program(self):
        # Get the directory of the current file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        bpf_file = os.path.join(current_dir, 'bpf_program.c')
        
        with open(bpf_file, 'r') as f:
            return f.read()

    def _monitor_events(self):
        # Compile and load eBPF program
        bpf_text = self._load_bpf_program()
        self.bpf = BPF(text=bpf_text)
        
        # Attach kprobes for exec
        execve_probes = [
            "__arm64_sys_execve",
            "do_execveat_common.isra.0",
            "bprm_execve"
        ]
        attached = False
        for probe in execve_probes:
            try:
                self.bpf.attach_kprobe(event=probe, fn_name="trace_exec")
                self.logger.info(f"Successfully attached to probe: {probe}")
                attached = True
                break
            except Exception as e:
                self.logger.warning(f"Failed to attach to probe {probe}: {str(e)}")
        if not attached:
            raise Exception("Could not attach to any execve probe points. Is BPF supported and enabled?")

        try:
            self.bpf.attach_kprobe(event="do_exit", fn_name="trace_exit")
        except Exception as e:
            self.logger.error(f"Failed to attach exit probe: {str(e)}")
        
        try:
            self.bpf.attach_tracepoint(tp="syscalls:sys_enter_kill", fn_name="tracepoint__syscalls__sys_enter_kill")
        except Exception as e:
            self.logger.error(f"Failed to attach enter kill tracepoint: {str(e)}")

        try:
            self.bpf.attach_tracepoint(tp="syscalls:sys_exit_kill", fn_name="tracepoint__syscalls__sys_exit_kill")
        except Exception as e:
            self.logger.error(f"Failed to attach exit kill tracepoint: {str(e)}")

        self.bpf["events"].open_perf_buffer(self._process_event)
        
        while self.running:
            try:
                self.bpf.perf_buffer_poll()
            except KeyboardInterrupt:
                break
    
    def _print_bpf_output(self):
        while self.running:
            try:
                with open("/sys/kernel/debug/tracing/trace_pipe", "rb") as f:
                    while self.running:
                        line = f.readline()
                        if line:
                            self.logger.debug(f"[BPF debug] {line.decode('utf-8', 'replace').strip()}")
            except Exception as e:
                self.logger.error(f"Error reading trace pipe: {e}")
                time.sleep(1)
    
    def start(self):
        """Start monitoring process events"""
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_events)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        
        self.trace_thread = threading.Thread(target=self._print_bpf_output)
        self.trace_thread.daemon = True
        self.trace_thread.start()
    
    def stop(self):
        """Stop monitoring process events"""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join()
        if self.bpf:
            self.bpf.cleanup()
    
    def get_events(self):
        """Return list of recorded events"""
        return self.events
    
    def get_process_kills(self):
        """Return list of kill events"""
        return [event for event in self.events if event['type'] == 'K'] 