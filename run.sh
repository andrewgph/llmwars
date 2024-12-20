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

# Build the Docker image locally
docker build -t promptwars .

# Save the Docker image to a tar file
docker save promptwars > promptwars.tar

# Helper functions for SSH/SCP commands
vm_ssh() {
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ./qemu_vm_files/vm_key -p 2224 myuser@localhost "$@"
}

vm_scp() {
    scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ./qemu_vm_files/vm_key -P 2224 "$@"
}

# Copy the Docker image to the VM
vm_scp promptwars.tar myuser@localhost:~/ || exit 1

# Load the Docker image on the VM
vm_ssh "docker load < ~/promptwars.tar" || exit 1

# Create a unique run directory
RUN_DIR="$SCRIPT_DIR/game_runs/run_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN_DIR"

# Run multiple games in parallel
for i in $(seq 1 $NUM_GAMES); do
    # Create unique game directory within the run directory
    GAME_DIR="$RUN_DIR/game_$i"
    mkdir -p "$GAME_DIR"
    
    echo "Created game directory: $GAME_DIR"
    
    # Create the directory on the VM
    vm_ssh "mkdir -p /tmp/game_$i" || exit 1
    
    # Run container on the VM
    vm_ssh "docker run --rm \
        --privileged \
        --cap-add ALL \
        -v /sys/kernel/debug:/sys/kernel/debug:rw \
        -v /lib/modules:/lib/modules:ro \
        -v /usr/src:/usr/src:ro \
        -v /tmp/game_$i:/shared_logs \
        --pid=host \
        promptwars \
        sh -c \"python3 -u game.py --game-timeout-seconds $TIMEOUT_SECONDS ${AGENT_CONFIGS[*]}\"" \
        > "$GAME_DIR/game.log" 2> "$GAME_DIR/game_err.log" &
done

# Wait for all background processes to complete
wait

# Copy logs back from VM for all completed games
for i in $(seq 1 $NUM_GAMES); do
    GAME_DIR="$RUN_DIR/game_$i"
    vm_scp -r 'myuser@localhost:/tmp/game_'"$i"'/*' "$GAME_DIR/" || echo "Warning: No logs found for game $i"
done

echo "All games completed"

# Run analysis script with the specific run directory
python3 analyze_games.py "$RUN_DIR"

# Cleanup
rm promptwars.tar

