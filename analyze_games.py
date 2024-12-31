import sys
from collections import defaultdict
from pathlib import Path
import json
import logging

def create_table(headers, data):
    # Calculate column widths
    widths = [len(str(h)) for h in headers]
    for row in data:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    
    # Create separator line
    separator = '+' + '+'.join('-' * (w + 2) for w in widths) + '+'
    
    # Format header
    result = [separator]
    header_row = '|' + '|'.join(f' {h:<{w}} ' for h, w in zip(headers, widths)) + '|'
    result.extend([header_row, separator])
    
    # Format data rows
    for row in data:
        data_row = '|' + '|'.join(f' {str(cell):<{w}} ' for cell, w in zip(row, widths)) + '|'
        result.append(data_row)
    
    result.append(separator)
    return '\n'.join(result)

def process_game_result(result_file, process_events_file):
    # Initialize game-specific stats
    game_stats = defaultdict(lambda: {
        'survived': 0, 
        'killed': 0, 
        'total': 0,
        'self_killed': 0,
        'killed_by_other': 0,
        'kills': 0
    })

    try:    
        with open(result_file) as f:
            result = json.load(f)
    except Exception as e:
        logging.error(f"Error reading {result_file}: {e}")
        return {}  # Return empty stats if there's an error
    
    try:
        with open(process_events_file) as f:
            process_events = json.load(f)
    except Exception as e:
        logging.error(f"Error reading {process_events_file}: {e}")
        return

    # Build process hierarchy and track agent processes
    agent_processes = {}  # Map of agent_id -> set of all child PIDs
    process_parent = {}   # Map of pid -> ppid
    
    # Initialize with root agent processes from game results
    for agent in result['agents']:
        agent_processes[agent['id']] = [agent['pid']]
        
    # Process exec events to build process hierarchy
    for event in process_events:
        if event['type'] == 'E':
            process_parent[event['pid']] = event['ppid']
            # Check if this is a child of any agent process
            for agent_id, pids in agent_processes.items():
                if event['ppid'] in pids:
                    pids.append(event['pid'])
    
    # Remove the top level su process from the hierarchy
    for agent_id, pids in agent_processes.items():
        pids.pop(0)

    logging.info(f"Agent processes: {agent_processes}")

    # Analyze kill events
    for agent in result['agents']:
        agent_key = (agent['name'], agent['id'])
        # The first two pids are a su and sh / python3 process, either being killed is fatal for the agent
        agent_root_pids = agent_processes[agent['id']][:2]
        
        # Find the kill event for this agent
        kill_events = [e for e in process_events if e['type'] == 'K' and e['kill_pid'] in agent_root_pids]
        
        if kill_events:
            killer_event = kill_events[0]
            killer_pid = killer_event['pid']
            
            for other_id, other_pids in agent_processes.items():
                if other_id != agent['id'] and killer_pid in other_pids:
                    game_stats[agent_key]['killed_by_other'] += 1
                    killer_agent_id = next((agent_id for agent_id, pids in agent_processes.items() 
                                          if killer_pid in pids), None)
                    if killer_agent_id:
                        killer_name = next(agent['name'] for agent in result['agents'] if agent['id'] == killer_agent_id)
                        game_stats[(killer_name, killer_agent_id)]['kills'] += 1
                    break
                elif other_id == agent['id'] and killer_pid in other_pids:
                    game_stats[agent_key]['self_killed'] += 1
                    break
            
            game_stats[agent_key]['killed'] += 1
        else:
            game_stats[agent_key]['survived'] += 1
        
        game_stats[agent_key]['total'] += 1

    return game_stats

def analyze_game_results(base_dir):
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )

    # Find all game directories
    game_dirs = [d for d in Path(base_dir).glob('game_*') if d.is_dir()]
    logging.info(f"Found {len(game_dirs)} game directories to analyze")
    
    # Store statistics for each agent
    stats = defaultdict(lambda: {
        'survived': 0, 
        'killed': 0, 
        'total': 0,
        'self_killed': 0,
        'killed_by_other': 0,
        'kills': 0
    })
    
    for game_dir in game_dirs:
        logging.info(f"Processing game directory: {game_dir}")
        result_file = game_dir / 'root_logs/game_result.json'
        if not result_file.exists():
            logging.warning(f"Missing game result file in {game_dir}")
            continue
        process_events_file = game_dir / 'root_logs/process_events.json'
        if not process_events_file.exists():
            logging.warning(f"Missing process events file in {game_dir}")
            continue
            
        # Get stats for this game and merge them
        game_stats = process_game_result(result_file, process_events_file)
        
        # Convert tuple keys to strings for JSON serialization
        serializable_stats = {f"{name}_{id}": stats for (name, id), stats in game_stats.items()}
        logging.info(f"Game results for {game_dir}: {json.dumps(serializable_stats, indent=2)}")
        
        # Merge game stats into overall stats
        for agent_key, agent_stats in game_stats.items():
            for stat_name, stat_value in agent_stats.items():
                stats[agent_key][stat_name] += stat_value

    # Prepare table data
    headers = ['Agent', 'ID', 'Survived', 'Killed', 'Self Kills', 'Opponent Kills', 'Kills', 'Total Games', 'Survival Rate']
    table_data = []
    
    for (agent_name, agent_id), data in stats.items():
        survival_rate = (data['survived'] / data['total'] * 100) if data['total'] > 0 else 0
        table_data.append([
            agent_name,
            agent_id,
            data['survived'],
            data['killed'],
            data['self_killed'],
            data['killed_by_other'],
            data['kills'],
            data['total'],
            f"{survival_rate:.1f}%"
        ])
    
    # Sort by survival rate (descending)
    table_data.sort(key=lambda x: float(x[8].rstrip('%')), reverse=True)
    
    # Print results
    print("\nGame Results Summary")
    print("===================")
    print(create_table(headers, table_data))
    print(f"\nTotal games analyzed: {len(game_dirs)}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Error: Please provide the run directory as an argument")
        print("Usage: python analyze_games.py <run_directory>")
        sys.exit(1)
    
    analyze_game_results(sys.argv[1])