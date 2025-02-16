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

// We use a map to stash kill args on sys_enter_kill
BPF_HASH(kill_args, u64, struct event_t);

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

// Hook sys_enter_kill: stash info about the kill attempt
TRACEPOINT_PROBE(syscalls, sys_enter_kill) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    
    struct event_t event = {};
    event.pid = pid_tgid >> 32;  // calling PID
    event.uid = bpf_get_current_uid_gid() >> 32;
    
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();
    event.ppid = task->real_parent->tgid;
    event.type = 'K';
    
    // Tracepoint format for sys_enter_kill gives us the target PID in args->pid
    event.kill_pid = args->pid;
    
    bpf_get_current_comm(&event.comm, sizeof(event.comm));
    
    // Save in map for lookup during sys_exit_kill
    kill_args.update(&pid_tgid, &event);
    return 0;
}

// Hook sys_exit_kill: check if kill succeeded
TRACEPOINT_PROBE(syscalls, sys_exit_kill) {
    // The return value (args->ret) indicates success (0) or failure (<0)
    u64 pid_tgid = bpf_get_current_pid_tgid();
    struct event_t *stored_event = kill_args.lookup(&pid_tgid);
    if (!stored_event) {
        return 0; 
    }
    
    // Only submit the event if the kill call was successful
    if (args->ret == 0) {
        events.perf_submit(args, stored_event, sizeof(*stored_event));
    }
    
    // Remove from the map to avoid leaking
    kill_args.delete(&pid_tgid);
    return 0;
}