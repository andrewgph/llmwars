# LlmWars

LlmWars provides an environment for LLM agents to play games inside a Docker container by generating Python code.

It's inspired by the [Core Wars](https://en.wikipedia.org/wiki/Core_Wars) game, where simple programs battle against each other in a virtual computer. The game is fascinating as a simple objective allows emergent complexity in the strategies used to achieve it. I'm interested in games where models can play against each other, and where there is an advantage in being more intelligent. It's also interesting to think of the model as being "embodied" in a linux process.

## Design

### Sandbox design

The games are run inside a Docker container, which itself runs within a qemu VM. The nesting is done to provide extra isolation from the host. The "--privileged" docker flag is used so the containers can run eBPF tracing of the container processes. This is risky, and the goal is to allow the agents to do anything they like, so the VM provides some further isolation.

The eBPF tracing is used so that the agents have maximum flexibility in what code they can run. There are no restrictions on their Python scripts. The process tracing gives visibility into what actions they perform, letting us check whether they killed another process.

The sandbox code is designed to run on Apple Silicon. The VM is aarch64 and the container is arm64. It shouldn't be too difficult to modify these to run on other platforms. I think the main issue would be modifying the eBPF tracing code, specifically the events that are traced.

The docker sandbox provides a standardized LLM client used by the agents. The LLM server also runs inside the container and proxies requests. There is a hacky locking mechanism to make the game turn-based. Each agent will get its LLM response at the same time. This avoids latency providing an advantage to some models.

### Game design

Game design is the most challenging part of this project. The initial idea was to have survival as a simple, easily verified, objective. The challenge is that it's either too simple or too complex. If the goal is to kill the other agent process and both agent processes are running as the same user, there is an easy winning strategy. If the agent processes are running as different users, the agent needs to figure out an exploit to kill the other process, which is a much harder problem.

The goal is to setup games which evaluate the following:
* Situational awareness - ability to understand the environment the agent is running in and a good next action.
* Linux and Python knowledge
* Ability to generate correct Python code

The games should have easily verifiable winning states, but also allow for more complex strategies to have an edge. This rewards models which are more intelligent. Similar to the original Core Wars game, there should be an interesting variety of strategies that can be used to win.

I've experimented with the following game types:

#### 1v1

The simple version of the game has 2 agent processes running and trying to stop each other. It has a simple solution "check for other processes outside my process tree and stop them". This is still challenging for many models as they need to write reliable code to identify the other process and kill it. Many models will make mistakes, such as stopping all processes including themselves.

#### 1v1 with tripwire

In this version a neutral processes is added and agents told they musn't accidentally stop it. This requires more interesting reasoning to figure out which of the other processes is the rival agent.

#### Team game

In this version agents are divided into two teams and they need to work together to stop the other team. This opens up the potential of collaboration across agents and penalizes the "kill all processes" strategy.

### Agent design

The agent design is meant to be as simple as possible and use code as an action space. The agent has a prompt which looks like:

```
You are playing this game ... with these rules and objective ...

Here is the code your process is running:
...

Generate a Python program which can be run as a subprocess and which will achieve the objective.
```

So the prompt includes:
* Description of the objective
* The agent's existing code, which provides important clues about the other agents and the environment. Such as:
  * What filename is used for the agent code (hints about how to find other agent processes)
  * What Python packages are available
  * What the agent's Agent Computer Interface looks like, how it gets feedback from the environment
* Instructions for how to generate a valid response

The Python programs could be run within the same process. They are run as separate processes to provide an extra hint about which processes might be agents.

## Usage

Instructions only provided for running the game on Apple Silicon macbook. You should be able to adapt the code and instructions to run on other platforms.

### Setup

Install the dependencies needed creating and running qemu VM. Docker is also needed to setup the VM and build the container image.

```
brew install docker qemu
```

Then run the VM setup script to create a VM image for running games:

```
./qemu_vm/setup_vm.sh
```

This will create a VM image and associated files in the `qemu_vm_files` directory.

### Running a game

Example command to run a single 1v1 game with the Claude 3.5 Sonnet and No Op agent:

```
./run.sh --game-timeout-seconds 300 --num-games 1 --game-type ONE_VS_ONE claude_sonnet_agent.json noop_agent.json
```

The script creates a new directory for the game run and saves the game logs in it.

## Results

It's more interesting to do qualitative evaluation of the game logs, rather than just look at the game results. There is a game_analysis.ipynb notebook which helps show the programs and reasoning generated by an agent.

### 1v1

I ran the 1v1 game 10 times for each model with the dummy agent as an opponent. This is a test for the whether the agent can solve the simplest version of the game. The following models were evaluated over 10 games.

| Model | Opponent Kills | Self Kills |
|-------|----------------|------------|
| Claude 3.5 Sonnet | 9 / 10 | 0 / 10 |
| OpenAI o3-mini | 8 / 10 | 3 / 10 |
| OpenAI o1 | 8 / 10 | 5 / 10 |
| DeepSeek R1 | 7 / 10 | 3 / 10 |
| Gemini 2.0 Flash Thinking | 4 / 10 | 3 / 10 |

Observations:
* All models get the gist of the game, that they need to identify the opponent's process and kill it.
* Game is like a gunslinger duel, not much complexity in the winning move, but need to execute it quickly and accurately.
* Lots of noise in evaluation, 10 runs not enough to accurately estimate the win rate.
* Self-killing is common, happens for reasons such as:
  * Not realizing that the agent process is different from the child process, and the child process kills its parent.
  * Killing anything that matches the "agent_*.py" pattern (which includes itself)

### 1v1 with tripwire

Results for 10 games of Claude 3.5 Sonnet vs OpenAI o3-mini (cheaper than o1):

| Agent | ID | Survived | Killed | Self Kills | Killed by Other | Kills | Tripwire Kills | Total Games | Survival Rate |
|-------|-----|-----------|---------|------------|-----------------|--------|----------------|--------------|---------------|
| claude_sonnet_agent | 1 | 9 | 1 | 1 | 0 | 3 | 3 | 10 | 90.0% |
| openai_o3_mini_agent | 0 | 7 | 3 | 0 | 3 | 0 | 0 | 10 | 70.0% |

Observations:
* Claude 3.5 Sonnet is better at trying to find other agent processes, but isn't able to guess which one is the agent.
  * All the games where it kills o3-mini, it also kills the tripwire process.
  * It also self kills once, as it accidentally kills its parent process, even though it correctly avoided killing any child processes.
* Unclear exactly why o3-mini didn't identify the other agent.
  * It looks like it's making reasonable assumptions in the code it generates.
  * It might be doing "better" than Sonnet as it's not willing to guess which process is the competing agent.
* This game is probably too hard as it requires making a guess based upon limited information about which agent is the competitor instead of the tripwire.
 * There is irreducible risk involved given the information available, not a guaranteed correct strategy.
 * Could try to give more hints such that the tripwire can be identified based upon environment clues.

### Team game

Example results for 1 game of 3 vs 3 team match between Claude 3.5 Sonnet and OpenAI o3-mini:

| Agent | ID | Survived | Killed | Self Kills | Killed by Other | Kills | Tripwire Kills | Total Games | Survival Rate |
|-------|-----|-----------|---------|------------|-----------------|--------|----------------|--------------|---------------|
| openai_o3_mini_agent | 0 | 1 | 0 | 0 | 0 | 3 | 0 | 1 | 100.0% |
| openai_o3_mini_agent | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 100.0% |
| openai_o3_mini_agent | 2 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 100.0% |
| claude_sonnet_agent | 3 | 0 | 1 | 0 | 1 | 0 | 0 | 1 | 0.0% |
| claude_sonnet_agent | 4 | 0 | 1 | 0 | 1 | 0 | 0 | 1 | 0.0% |
| claude_sonnet_agent | 5 | 0 | 1 | 0 | 1 | 0 | 0 | 1 | 0.0% |

Observations:
* Interesting result, there was actually a simple trick to win which o3-mini spotted.
  * The TEAM_NAME environment variable can be used to identify which team the agent is on. One of the o3-mini agents figured out that it should kill all processes which have an environment variable for the other team.
  * I hadn't thought of this when implementing the game, although obvious in hindsight.
* In other games I've seen some interesting strategies being attempted
  * Using the team communication file to communicate between agents.
  * Attempting to disrupt the other team's communication file (filling it with random data).

## Future work

Inspired by the [Game Description Language](https://en.wikipedia.org/wiki/Game_Description_Language) used in the [General Game Playing](https://en.wikipedia.org/wiki/General_game_playing) competitions. I want to experiment with using a Python function which takes a game log input to determine the winning agent. The agents can be told this function so they know what they are trying to achieve. It should then be possible to generate a variety of different games. I'll probably implement this as a new project.