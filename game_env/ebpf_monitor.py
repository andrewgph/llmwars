from bcc import BPF
import os
import json
import time
from datetime import datetime
import threading
import sys

# eBPF program
bpf_text = """
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>
#include <linux/signal.h>
#include <asm/ptrace.h>

// Data structure to store process events
struct event_t {
    u32 pid;        // Process ID
    u32 ppid;       // Parent process ID
    u32 uid;        // User ID
    u32 kill_pid;   // Target PID for kill events
    char comm[16];  // Process name
    char type;      // Event type: 'E' exec, 'X' exit, 'K' kill
};

BPF_PERF_OUTPUT(events);

// Track process executions
int trace_exec(struct pt_regs *ctx) {
    struct event_t event = {};
    
    u64 pid_tgid = bpf_get_current_pid_tgid();
    event.pid = pid_tgid >> 32;
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();
    event.ppid = task->real_parent->tgid;  // Get actual PPID
    event.uid = bpf_get_current_uid_gid() >> 32;
    event.type = 'E';
    bpf_get_current_comm(&event.comm, sizeof(event.comm));
    
    events.perf_submit(ctx, &event, sizeof(event));
    return 0;
}

// Track process exits
int trace_exit(struct pt_regs *ctx) {
    struct event_t event = {};
    
    u64 pid_tgid = bpf_get_current_pid_tgid();
    event.pid = pid_tgid >> 32;
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();
    event.ppid = task->real_parent->tgid;  // Get actual PPID
    event.uid = bpf_get_current_uid_gid() >> 32;
    event.type = 'X';
    bpf_get_current_comm(&event.comm, sizeof(event.comm));
    
    events.perf_submit(ctx, &event, sizeof(event));
    return 0;
}

// ------------------------------------------------------------------
// Switch from a kprobe to a tracepoint for kill syscalls (ARM64).
//
// The tracepoint "syscalls:sys_enter_kill" has a well-defined layout
// for arguments, ensuring we capture the correct PID. We name the
// function "trace_kill_tp" to differentiate it from the older approach.
// ------------------------------------------------------------------
TRACEPOINT_PROBE(syscalls, sys_enter_kill) {
    struct event_t event = {};
    u64 pid_tgid = bpf_get_current_pid_tgid();
    event.pid = pid_tgid >> 32;  // current PID
    event.uid = bpf_get_current_uid_gid() >> 32;
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();
    event.ppid = task->real_parent->tgid;  // actual PPID
    event.type = 'K';

    // Tracepoint format for sys_enter_kill:
    // args->pid = the PID argument
    event.kill_pid = args->pid;

    bpf_get_current_comm(&event.comm, sizeof(event.comm));
    events.perf_submit(args, &event, sizeof(event));
    return 0;
}
"""

class EbpfMonitor:
    def __init__(self, log_dir):
        self.log_dir = log_dir
        self.events_log_path = os.path.join(log_dir, 'process_events.json')
        self.events = []
        self.bpf = None
        self.running = False
        self.monitor_thread = None
        
        # Initialize empty events log
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
    
    def _monitor_events(self):
        # Compile and load eBPF program
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
                print(f"Successfully attached to probe: {probe}")
                attached = True
                break
            except Exception as e:
                print(f"Failed to attach to probe {probe}: {str(e)}")
        if not attached:
            raise Exception("Could not attach to any execve probe points. Is BPF supported and enabled?")
        
        # Attach kprobe for do_exit
        try:
            self.bpf.attach_kprobe(event="do_exit", fn_name="trace_exit")
        except Exception as e:
            print(f"Failed to attach exit probe: {str(e)}")
        
        # ----------------------------------------------------------------
        # Instead of attaching a kprobe to __arm64_sys_kill,
        # attach a tracepoint for kill syscalls:
        # ----------------------------------------------------------------
        try:
            self.bpf.attach_tracepoint(tp="syscalls:sys_enter_kill", fn_name="trace_kill_tp")
        except Exception as e:
            print(f"Failed to attach kill tracepoint: {str(e)}")
        
        # Open perf buffer
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
                            print("[BPF debug]", line.decode('utf-8', 'replace'), end="", file=sys.stderr)
            except Exception as e:
                print(f"Error reading trace pipe: {e}", file=sys.stderr)
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