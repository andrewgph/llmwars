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

// Switch from a kprobe to a tracepoint for kill syscalls (ARM64).
//
// The tracepoint "syscalls:sys_enter_kill" has a well-defined layout
// for arguments, ensuring we capture the correct PID. We name the
// function "trace_kill_tp" to differentiate it from the older approach.
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