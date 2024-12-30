import sys
from collections import defaultdict
from pathlib import Path
import json

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

def analyze_game_results(base_dir):
    # Find all game directories in the specific run directory
    game_dirs = [d for d in Path(base_dir).glob('game_*') if d.is_dir()]
    
    # Store statistics for each agent
    stats = defaultdict(lambda: {'wins': 0, 'losses': 0, 'total': 0})
    
    for game_dir in game_dirs:
        result_file = game_dir / 'root_logs/game_result.json'
        if not result_file.exists():
            continue
            
        try:
            with open(result_file) as f:
                result = json.load(f)
                
            # Count agents that were not stopped as winners
            agents = result['agents']
            winners = [agent for agent in agents if not agent['was_stopped']]
            losers = [agent for agent in agents if agent['was_stopped']]
            
            # Update statistics using composite key
            for agent in winners:
                agent_key = (agent['name'], agent['id'])  # Tuple of name and ID as key
                stats[agent_key]['wins'] += 1
                stats[agent_key]['total'] += 1
                
            for agent in losers:
                agent_key = (agent['name'], agent['id'])  # Tuple of name and ID as key
                stats[agent_key]['losses'] += 1
                stats[agent_key]['total'] += 1
                
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Error reading {result_file}: {e}")
            continue

    # Prepare table data
    headers = ['Agent', 'ID', 'Wins', 'Losses', 'Total Games', 'Win Rate']
    table_data = []
    
    for (agent_name, agent_id), data in stats.items():
        win_rate = (data['wins'] / data['total'] * 100) if data['total'] > 0 else 0
        table_data.append([
            agent_name,
            agent_id,
            data['wins'],
            data['losses'],
            data['total'],
            f"{win_rate:.1f}%"
        ])
    
    # Sort by win rate (descending)
    table_data.sort(key=lambda x: float(x[5].rstrip('%')), reverse=True)
    
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