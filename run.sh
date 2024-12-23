#!/bin/bash

# Get the directory where the script is located
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

# Parse command line arguments
NUM_GAMES=1  # Default value
TIMEOUT_SECONDS=60  # Default value
AGENT_CONFIGS=()  # Initialize empty array
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
        *)
            AGENT_CONFIGS+=("$1")
            shift
            ;;
    esac
done

# Check if at least one agent config is provided
if [ ${#AGENT_CONFIGS[@]} -lt 1 ]; then
    echo "Usage: $0 [--num-games N] [--game-timeout-seconds T] <agent1_config.json> [agent2_config.json] [agent3_config.json] ..."
    echo "Options:"
    echo "  --num-games N              Number of parallel games to run (default: 1)"
    echo "  --game-timeout-seconds T   Maximum duration for each game in seconds (default: 60)"
    echo "Provide at least one agent configuration file"
    exit 1
fi

# Add VM configuration variables
VM_NAME="promptwars-vm"
CPUS=4  # Adjust as needed
RAM="4G"  # Adjust as needed
DISK_FILE="$SCRIPT_DIR/qemu_vm_files/ubuntu-vm.qcow2"
ISO_FILE="$SCRIPT_DIR/qemu_vm_files/cloud-init.iso"

# Helper functions for SSH/SCP commands
vm_ssh() {
    ssh -q \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ServerAliveInterval=60 \
        -o ServerAliveCountMax=10 \
        -i ./qemu_vm_files/vm_key \
        -p 2224 myuser@localhost "$@"
}

vm_scp() {
    scp -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ./qemu_vm_files/vm_key -P 2224 "$@"
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

# Start the VM in the background and save its PID
qemu-system-aarch64 \
    -name "$VM_NAME" \
    -machine virt \
    -accel hvf \
    -cpu cortex-a72 \
    -smp "$CPUS" \
    -m "$RAM" \
    -bios /opt/homebrew/share/qemu/edk2-aarch64-code.fd \
    -drive if=virtio,file="$DISK_FILE" \
    -cdrom "$ISO_FILE" \
    -device virtio-net-pci,netdev=net0 \
    -netdev user,id=net0,hostfwd=tcp::2224-:22 \
    -device virtio-serial \
    -chardev socket,path=/tmp/qga.sock,server=on,wait=off,id=qga0 \
    -device virtserialport,chardev=qga0,name=org.qemu.guest_agent.0 \
    -nographic > "$RUN_DIR/vm.log" 2>&1 &
VM_PID=$!

# Function to cleanup VM on script exit
cleanup() {
    echo "Cleaning up..."
    if [ -n "$VM_PID" ]; then
        kill $VM_PID
        wait $VM_PID 2>/dev/null
    fi
}

# Set trap to ensure VM is stopped when script exits
trap cleanup EXIT

# Wait for VM to boot and SSH to become available
echo "Waiting for VM to boot..."
MAX_RETRIES=60  # Maximum number of retries (e.g., 5 minutes with 5-second sleep)
RETRY_COUNT=0
while ! vm_ssh "systemctl is-system-running" >/dev/null 2>&1; do
    sleep 5
    RETRY_COUNT=$((RETRY_COUNT + 1))
    
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "Error: VM failed to boot within timeout period"
        exit 1
    fi
    
    echo "Still waiting for VM to boot... (attempt $RETRY_COUNT/$MAX_RETRIES)"
done
echo "VM is ready"

# Generate a unique run ID (8 character random hex)
RUN_ID=$(openssl rand -hex 4)



# Build the Docker image locally
docker build -t promptwars .

# Save the Docker image to a temporary tar file
TEMP_TAR="/tmp/promptwars_${RUN_ID}.tar"
docker save promptwars > "$TEMP_TAR"

# Copy the Docker image to the VM
vm_scp "$TEMP_TAR" myuser@localhost:~/ || exit 1

# Load the Docker image on the VM
vm_ssh "docker load < ~/promptwars_${RUN_ID}.tar" || exit 1

# Cleanup
rm "$TEMP_TAR"

# Run multiple games in parallel
for i in $(seq 1 $NUM_GAMES); do
    # Create unique game directory within the run directory
    GAME_DIR="$RUN_DIR/game_$i"
    mkdir -p "$GAME_DIR"
    
    echo "Created game directory: $GAME_DIR"
    
    # Create the directory on the VM using run ID
    vm_ssh "mkdir -p /tmp/$RUN_ID/game_$i" || exit 1
    
    # Run container on the VM
    vm_ssh "docker run --rm \
        --privileged \
        --cap-add ALL \
        -v /sys/kernel/debug:/sys/kernel/debug:rw \
        -v /lib/modules:/lib/modules:ro \
        -v /usr/src:/usr/src:ro \
        -v /tmp/$RUN_ID/game_$i:/shared_logs \
        --pid=host \
        promptwars \
        sh -c \"python3 -u game.py --game-timeout-seconds $TIMEOUT_SECONDS ${AGENT_CONFIGS[*]}\" \
        > /tmp/$RUN_ID/game_$i/game.log \
        2> /tmp/$RUN_ID/game_$i/game_err.log" \
        </dev/null > "$GAME_DIR/ssh.log" 2> "$GAME_DIR/ssh_err.log" &
done

# Wait for all games to complete by checking for running containers
echo "Waiting for games to complete..."
while true; do
    sleep 10
    RUNNING_CONTAINERS=$(vm_ssh "docker ps --filter ancestor=promptwars -q" | wc -l)
    if [ "$RUNNING_CONTAINERS" -eq 0 ]; then
        echo "All games finished"
        break
    else
        echo "Still running: $RUNNING_CONTAINERS containers..."
    fi
done

# Copy logs back from VM for all completed games
for i in $(seq 1 $NUM_GAMES); do
    GAME_DIR="$RUN_DIR/game_$i"
    vm_scp -r "myuser@localhost:/tmp/$RUN_ID/game_$i/*" "$GAME_DIR/" || echo "Warning: No logs found for game $i"
done

echo "All games completed"

# Run analysis script with the specific run directory
python3 analyze_games.py "$RUN_DIR"
