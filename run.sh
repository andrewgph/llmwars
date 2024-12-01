#!/bin/bash

# Get the directory where the script is located
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

# Build the Docker image once
docker build -t promptwars .

# Number of parallel games to run
NUM_GAMES=10

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
        claude_sonnet_agent.json random_kill_agent.json > "$GAME_DIR/game.log" 2> "$GAME_DIR/game_err.log" &
done

# Wait for all background processes to complete
wait

echo "All games completed"

# Run analysis script with the specific run directory
python3 analyze_games.py "$RUN_DIR"

