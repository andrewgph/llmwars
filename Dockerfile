# Use Ubuntu base image
FROM ubuntu:22.04

# Install required packages including Python and monitoring tools
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    procps \
    inotify-tools \
    acct \
    bpfcc-tools \
    python3-bpfcc \
    linux-headers-6.8.0-49-generic \
    && rm -rf /var/lib/apt/lists/*

# Create directories
RUN mkdir -p /root_space /agent_space /agent_logs /root_logs

# Use 1777 to allow all users to write to the shared agent logs 
RUN chmod 1777 /agent_logs

# Set root-only permissions for root_logs
RUN chmod 700 /root_logs

# Create a new user called 'agent' and set permissions
RUN useradd -m agent && \
    chown -R agent:agent /agent_space && \
    chmod 755 /agent_space

# Set the working directory
WORKDIR /root_space

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip3 install -r requirements.txt

# Copy root space files
COPY game_env/* ./
COPY agent_configs ./agent_configs
COPY .env ./

# Copy agent space files
COPY agents/* /agent_space/
COPY game_env/llm_client.py /agent_space/
COPY agent_configs/* /agent_space/
COPY requirements.txt /agent_space/

# Set environment variables
# ENV PYTHONPATH=/root_space
ENV AGENT_LOGS=/agent_logs
ENV ROOT_LOGS=/root_logs
ENV ROOT_SPACE=/root_space
ENV AGENT_SPACE=/agent_space
ENV AGENT_USER=agent