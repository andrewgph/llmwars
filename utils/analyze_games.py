import sys
from collections import defaultdict
from pathlib import Path
import json
import logging

def create_table(headers, data):
    """
    Creates a table as a string, given headers and data (a list of lists).
    """
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

def process_game_result(game_dir):
    """
    Reads a single game's result and process events, then compiles stats for each agent.
    """
    result_file = game_dir / 'root_logs/game_result.json'
    if not result_file.exists():
        raise FileNotFoundError(f"Missing game result file in {game_dir}")

    process_events_file = game_dir / 'root_logs/process_events.json'
    if not process_events_file.exists():
        raise FileNotFoundError(f"Missing process events file in {game_dir}")

    try:
        with open(result_file) as f:
            game_result = json.load(f)
    except Exception as e:
        raise Exception(f"Error reading {result_file}: {e}")

    try:
        with open(process_events_file) as f:
            process_events = json.load(f)
    except Exception as e:
        raise Exception(f"Error reading {process_events_file}: {e}")

    # Initialize game-specific stats (including the new tripwire_kills field)
    game_stats = defaultdict(lambda: {
        'survived': 0,
        'killed': 0,
        'total': 0,
        'self_killed': 0,
        'killed_by_other': 0,
        'kills': 0,
        'tripwire_kills': 0
    })

    # Build process hierarchy and track agent processes
    # agent_processes: Dict[agent_id, List[pid]]
    agent_processes = {}

    # Initialize with root agent processes from game results
    for agent in game_result['agents']:
        agent_processes[agent['id']] = [agent['pid']]

    # Populate child PIDs via events
    for event in process_events:
        if event['type'] == 'E':
            ppid = event['ppid']
            pid = event['pid']
            for a_id, pid_list in agent_processes.items():
                if ppid in pid_list:
                    pid_list.append(pid)

    # Remove the top-level su process from each agent's list
    # (Assumes the first item in agent_processes is su or similar)
    # We do a defensive check in case any list is empty.
    for a_id, pid_list in agent_processes.items():
        if pid_list:
            pid_list.pop(0)

    logging.info(f"Agent processes: {agent_processes}")

    # Analyze kill events
    for agent in game_result['agents']:
        # Agent key in the stats dictionary
        agent_key = (agent['name'], agent['id'])

        # We assume the next two PIDs are su+sh or similar root processes.
        # This is fragile if the chain differs from su->sh->python3
        agent_root_pids = agent_processes[agent['id']][:2] if agent['id'] in agent_processes else []

        # Find kill events for these root pids (we only consider the first kill event encountered)
        kill_events = [
            evt for evt in process_events
            if evt['type'] == 'K' and evt.get('kill_pid') in agent_root_pids
        ]

        if kill_events:
            # We only consider the first relevant kill
            killer_event = kill_events[0]
            killer_pid = killer_event['pid']

            # Identify which agent did the killing (if any)
            for other_agent_id, pid_list in agent_processes.items():
                if other_agent_id != agent['id'] and killer_pid in pid_list:
                    # The agent was killed by another agent
                    game_stats[agent_key]['killed_by_other'] += 1

                    actual_killer_agent_id = next(
                        (a_id for a_id, pids in agent_processes.items() if killer_pid in pids),
                        None
                    )
                    if actual_killer_agent_id is not None:
                        killer_agent_data = next(
                            (ag for ag in game_result['agents'] if ag['id'] == actual_killer_agent_id),
                            None
                        )
                        if killer_agent_data is not None:
                            killer_name = killer_agent_data['name']
                            # If the killed agent was a tripwire, increment tripwire_kills
                            if agent.get('is_tripwire', False):
                                game_stats[(killer_name, actual_killer_agent_id)]['tripwire_kills'] += 1
                            else:
                                game_stats[(killer_name, actual_killer_agent_id)]['kills'] += 1
                    break
                elif other_agent_id == agent['id'] and killer_pid in pid_list:
                    # The agent killed itself
                    game_stats[agent_key]['self_killed'] += 1
                    break

            game_stats[agent_key]['killed'] += 1
        else:
            # No kill event => agent survived
            game_stats[agent_key]['survived'] += 1

        # Agent always has a 'total' increment (survived or killed)
        game_stats[agent_key]['total'] += 1

    # Compare game_stats with the game_result
    for agent in game_result['agents']:
        agent_key = (agent['name'], agent['id'])
        
        # Compare was_killed with our calculated stats
        was_killed = agent['was_killed']
        calculated_killed = game_stats[agent_key]['killed'] > 0
        
        if was_killed != calculated_killed:
            raise ValueError(
                f"Inconsistency detected for agent {agent['name']} (ID: {agent['id']}) in {game_dir}:\n"
                f"Game result shows was_killed={was_killed}, but calculated stats show "
                f"killed={calculated_killed} (survived={game_stats[agent_key]['survived']}, "
                f"killed={game_stats[agent_key]['killed']})"
            )

    # Remove tripwire agents from game_stats
    tripwire_keys = [
        agent_key for agent_key in list(game_stats.keys())
        if next((a for a in game_result['agents'] if a['id'] == agent_key[1] and a.get('is_tripwire', False)), None)
    ]
    for key in tripwire_keys:
        del game_stats[key]

    return game_stats

def analyze_game_results(base_dir):
    """
    Main entry point. Scans all 'game_*' directories under base_dir, compiles stats, and prints a summary.
    """

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )

    # Find all game directories
    game_dirs = [d for d in Path(base_dir).glob('game_*') if d.is_dir()]
    logging.info(f"Found {len(game_dirs)} game directories to analyze")

    # Store cumulative statistics for each (agent_name, agent_id) across all games
    cumulative_stats = defaultdict(lambda: {
        'survived': 0,
        'killed': 0,
        'total': 0,
        'self_killed': 0,
        'killed_by_other': 0,
        'kills': 0,
        'tripwire_kills': 0
    })

    # Track any agent IDs that appear as tripwire
    tripwire_agents = set()

    # Process each game directory
    for game_dir in game_dirs:
        logging.info(f"Processing game directory: {game_dir}")



        # Get this game's stats
        game_stats = process_game_result(game_dir)
        if not game_stats:
            # If process_game_result failed or returned an empty dict, skip
            continue

        # Convert the per-game results for logging
        serializable_stats = {
            f"{agent_name}_{agent_id}": stats_dict
            for (agent_name, agent_id), stats_dict in game_stats.items()
        }
        logging.info(f"Game results for {game_dir}: {json.dumps(serializable_stats, indent=2)}")

        # Merge game stats into cumulative
        for (agent_name, agent_id), agent_stats in game_stats.items():
            for stat_name, stat_value in agent_stats.items():
                cumulative_stats[(agent_name, agent_id)][stat_name] += stat_value

    # Prepare table data
    headers = [
        'Agent', 'ID', 'Survived', 'Killed', 'Self Kills',
        'Killed by Other', 'Kills', 'Tripwire Kills', 'Total Games', 'Survival Rate'
    ]
    table_data = []

    # Build rows for non-tripwire agents
    for (agent_name, agent_id), data in cumulative_stats.items():
        total_games = data['total']
        survived = data['survived']
        survival_rate = (survived / total_games * 100) if total_games > 0 else 0.0

        table_data.append([
            agent_name,
            agent_id,
            data['survived'],
            data['killed'],
            data['self_killed'],
            data['killed_by_other'],
            data['kills'],
            data['tripwire_kills'],
            total_games,
            f"{survival_rate:.1f}%"
        ])

    # Sort by survival rate descending
    survival_rate_idx = headers.index('Survival Rate')
    table_data.sort(key=lambda row: float(row[survival_rate_idx].rstrip('%')), reverse=True)

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