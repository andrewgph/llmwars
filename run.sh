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

# Build the Docker image once
docker build -t promptwars .

# Create a unique run directory
RUN_DIR="$SCRIPT_DIR/game_runs/run_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN_DIR"

# Run multiple games in parallel
for i in $(seq 1 $NUM_GAMES); do
    # Create unique game directory within the run directory
    GAME_DIR="$RUN_DIR/game_$i"
    mkdir -p "$GAME_DIR"
    
    echo "Created game directory: $GAME_DIR"
    
    # Run container in background and redirect stdout/stderr to log files
    docker run --rm \
        -v "$GAME_DIR:/shared_logs" \
        promptwars \
        --game-timeout-seconds "$TIMEOUT_SECONDS" \
        "${AGENT_CONFIGS[@]}" > "$GAME_DIR/game.log" 2> "$GAME_DIR/game_err.log" &
done

# Wait for all background processes to complete
wait

echo "All games completed"

# Run analysis script with the specific run directory
python3 analyze_games.py "$RUN_DIR"

