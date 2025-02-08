#!/bin/bash

# Get the directory where the script is located
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
QEMU_VM_DIR="$SCRIPT_DIR/qemu_vm/files"

# Parse command line arguments
NUM_GAMES=1
TIMEOUT_SECONDS=300
SIMULTANEOUS_TURNS_ARG="--simultaneous-turns" 
# Whether to delete the VM disk file after the run
# Useful to keep the disk file around when debugging
DELETE_RUN_VM_DISK=true
GAME_TYPE="ONE_VS_ONE"  # Add default game type
MAX_TURNS=10  # Add default max turns
AGENT_CONFIGS=()
while [[ $# -gt 0 ]]; do
    case $1 in
        --num-games)
            NUM_GAMES="$2"
            shift 2
            ;;
        --game-timeout-seconds)
            TIMEOUT_SECONDS="$2"
            shift 2
            ;;
        --delete-run-vm-disk)
            DELETE_RUN_VM_DISK="$2"
            shift 2
            ;;
        --game-type)
            GAME_TYPE="$2"
            shift 2
            ;;
        --max-turns)
            MAX_TURNS="$2"
            shift 2
            ;;
        *)
            AGENT_CONFIGS+=("$1")
            shift
            ;;
    esac
done

# Add log function near the top of the script, after the initial variable declarations
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# Check if at least one agent config is provided
if [ ${#AGENT_CONFIGS[@]} -lt 1 ]; then
    echo "Usage: $0 [--num-games N] [--game-timeout-seconds T] [--simultaneous-turns true|false] [--game-type TYPE] [--max-turns M] <agent1_config.json> [agent2_config.json] [agent3_config.json] ..."
    echo "Options:"
    echo "  --num-games N              Number of parallel games to run (default: 1)"
    echo "  --game-timeout-seconds T   Maximum duration for each game in seconds (default: 60)"
    echo "  --simultaneous-turns       Whether to allow simultaneous turns (default: true)"
    echo "  --game-type TYPE          Game type to run (default: ONE_VS_ONE_WITH_TRIPWIRE)"
    echo "  --max-turns M             Maximum turns for each game (default: 10)"
    echo "Provide at least one agent configuration file"
    exit 1
fi

# Add VM configuration variables
VM_NAME="promptwars-vm"
CPUS="4"
RAM="4G"
DISK_FILE="$QEMU_VM_DIR/ubuntu-vm.qcow2"
ISO_FILE="$QEMU_VM_DIR/cloud-init.iso"

# Helper functions for SSH/SCP commands
vm_ssh() {
    ssh -q \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ServerAliveInterval=60 \
        -o ServerAliveCountMax=10 \
        -i "$QEMU_VM_DIR/vm_key" \
        -p 2224 myuser@localhost "$@"
}

vm_scp() {
    scp -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "$QEMU_VM_DIR/vm_key" -P 2224 "$@"
}

# Check if required VM files exist
if [ ! -f "$DISK_FILE" ]; then
    echo "Error: VM disk file not found: $DISK_FILE"
    exit 1
fi

if [ ! -f "$ISO_FILE" ]; then
    echo "Error: Cloud-init ISO file not found: $ISO_FILE"
    exit 1
fi

# Create a unique run directory
RUN_DIR="$SCRIPT_DIR/game_runs/run_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN_DIR"

# Function to cleanup VM on script exit
cleanup() {
    log "Cleaning up VM..."
    if [ -n "$VM_PID" ]; then
        kill $VM_PID
        wait $VM_PID 2>/dev/null
    fi
    if [ -n "$RUN_DISK_FILE" ] && [ -f "$RUN_DISK_FILE" ] && [ "$DELETE_RUN_VM_DISK" = true ]; then
        rm "$RUN_DISK_FILE"
    fi
}

# Create a temporary copy of VM disk file for this run
RUN_DISK_FILE="$RUN_DIR/ubuntu-vm.qcow2"
log "Creating temporary copy of VM disk file for this run at $RUN_DISK_FILE"
cp "$DISK_FILE" "$RUN_DISK_FILE"

# Start the VM in the background and save its PID
qemu-system-aarch64 \
    -name "$VM_NAME" \
    -machine virt \
    -accel hvf \
    -cpu cortex-a72 \
    -smp "$CPUS" \
    -m "$RAM" \
    -bios /opt/homebrew/share/qemu/edk2-aarch64-code.fd \
    -drive if=virtio,file="$RUN_DISK_FILE" \
    -cdrom "$ISO_FILE" \
    -device virtio-net-pci,netdev=net0 \
    -netdev user,id=net0,hostfwd=tcp::2224-:22 \
    -device virtio-serial \
    -chardev socket,path=/tmp/qga.sock,server=on,wait=off,id=qga0 \
    -device virtserialport,chardev=qga0,name=org.qemu.guest_agent.0 \
    -nographic > "$RUN_DIR/vm.log" 2>&1 &
VM_PID=$!

# Set trap to ensure VM is stopped when script exits
trap cleanup EXIT

# Wait for VM to boot and SSH to become available
log "Waiting for VM to boot..."
MAX_RETRIES=60  # Maximum number of retries (e.g., 5 minutes with 5-second sleep)
RETRY_COUNT=0
while ! vm_ssh "systemctl is-system-running" >/dev/null 2>&1; do
    sleep 5
    RETRY_COUNT=$((RETRY_COUNT + 1))
    
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        log "Error: VM failed to boot within timeout period"
        exit 1
    fi
    
    log "Still waiting for VM to boot... (attempt $RETRY_COUNT/$MAX_RETRIES)"
done
log "VM is ready"

# Generate a unique run ID (8 character random hex)
RUN_ID=$(openssl rand -hex 4)

# Build the Docker image locally
docker build -t llmwars .

# Save the Docker image to a temporary tar file
TEMP_TAR="/tmp/llmwars_${RUN_ID}.tar"
docker save llmwars > "$TEMP_TAR"

# Copy the Docker image to the VM
vm_scp "$TEMP_TAR" myuser@localhost:~/ || exit 1

# Load the Docker image on the VM
vm_ssh "docker load < ~/llmwars_${RUN_ID}.tar" || exit 1

# Cleanup
rm "$TEMP_TAR"

# Run games sequentially
for i in $(seq 1 $NUM_GAMES); do
    # Create unique game directory within the run directory
    GAME_DIR="$RUN_DIR/game_$i"
    mkdir -p "$GAME_DIR"
    
    log "Starting game $i of $NUM_GAMES"
    log "Created game directory: $GAME_DIR"
    
    # Create the directory on the VM using run ID
    vm_ssh "mkdir -p /tmp/$RUN_ID/game_$i/agent_logs /tmp/$RUN_ID/game_$i/root_logs" || exit 1
    
    # Run container on the VM and wait for it to complete
    vm_ssh "docker run --rm \
        --privileged \
        --cap-add ALL \
        -v /sys/kernel/debug:/sys/kernel/debug:rw \
        -v /lib/modules:/lib/modules:ro \
        -v /usr/src:/usr/src:ro \
        -v /tmp/$RUN_ID/game_$i/agent_logs:/agent_logs \
        -v /tmp/$RUN_ID/game_$i/root_logs:/root_logs \
        --pid=host \
        llmwars \
        sh -c \"python3 -u game.py --game-timeout-seconds $TIMEOUT_SECONDS $SIMULTANEOUS_TURNS_ARG --game-type $GAME_TYPE --max-turns $MAX_TURNS ${AGENT_CONFIGS[*]}\" \
        > /tmp/$RUN_ID/game_$i/game.log \
        2> /tmp/$RUN_ID/game_$i/game_err.log"
    
    # Wait for game logs
    sleep 10

    # Copy logs back from VM for the completed game
    vm_scp -r "myuser@localhost:/tmp/$RUN_ID/game_$i/*" "$GAME_DIR/" || echo "Warning: No logs found for game $i"
    
    log "Completed game $i of $NUM_GAMES"
done

log "All games completed"

# Run analysis script with the specific run directory
python3 analyze_games.py "$RUN_DIR"
