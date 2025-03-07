#!/usr/bin/env python3
'''
Setup script for the satellite network simulator.
Creates the necessary Docker directories and files.
'''

import os
import sys
import shutil
import subprocess
import configparser
from pathlib import Path

# Detect script directory and set up paths
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT =  SCRIPT_DIR#SCRIPT_DIR.parent
DOCKER_DIR = PROJECT_ROOT / 'docker'
CONFIG_FILE = PROJECT_ROOT / 'emulation' / 'mnet' / 'configs' / 'small.net'

def create_directories():
    """Create necessary directories for Docker setup"""
    print("Creating Docker directories...")
    
    directories = [
        DOCKER_DIR,
        DOCKER_DIR / 'node',
        DOCKER_DIR / 'controller',
        DOCKER_DIR / 'dynamics',
        PROJECT_ROOT / 'logs',
        PROJECT_ROOT / 'data'
    ]
    
    for directory in directories:
        directory.mkdir(exist_ok=True)
        print(f"Created directory: {directory}")


def create_node_image():
    """Create the base node image Dockerfile"""
    dockerfile = DOCKER_DIR / 'node' / 'Dockerfile'
    
    with open(dockerfile, 'w') as f:
        f.write('''FROM ubuntu:22.04

# Set noninteractive installation
ENV DEBIAN_FRONTEND=noninteractive

# Install required packages
RUN apt-get update && apt-get install -y \\
    python3 \\
    python3-pip \\
    python3-dev \\
    iputils-ping \\
    net-tools \\
    iproute2 \\
    tcpdump \\
    frr \\
    supervisor

# Install Python dependencies
COPY requirements-node.txt /app/
RUN pip3 install -r /app/requirements-node.txt

# Configure FRR
RUN sed -i 's/bgpd=no/bgpd=yes/g' /etc/frr/daemons && \\
    sed -i 's/ospfd=no/ospfd=yes/g' /etc/frr/daemons && \\
    sed -i 's/zebra=no/zebra=yes/g' /etc/frr/daemons && \\
    sed -i 's/staticd=no/staticd=yes/g' /etc/frr/daemons

# Copy application code
COPY emulation/node_agent.py /app/emulation/
COPY emulation/common /app/emulation/common/

# Create necessary directories
RUN mkdir -p /app/data /app/logs /app/configs

# Set working directory
WORKDIR /app

# Copy supervisor configuration for services
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose ports
EXPOSE 8080 179 2601 2604 2605 5000

CMD ["/usr/bin/supervisord"]
''')
    
    print(f"Created Node Dockerfile: {dockerfile}")


def create_controller_image():
    """Create the controller image Dockerfile"""
    dockerfile = DOCKER_DIR / 'controller' / 'Dockerfile'
    
    with open(dockerfile, 'w') as f:
        f.write('''FROM python:3.10-slim

# Install required packages
RUN apt-get update && apt-get install -y \\
    iputils-ping \\
    net-tools \\
    curl \\
    docker.io \\
    nodejs \\
    npm

# Install Python dependencies
COPY requirements-controller.txt /app/
RUN pip install -r /app/requirements-controller.txt

# Copy application code
COPY emulation /app/emulation/

# Set up the web interface
WORKDIR /app/emulation/mnet/static/js
RUN npm install && npm run build

# Set working directory back to /app
WORKDIR /app

# Create necessary directories
RUN mkdir -p /app/data /app/logs

# Expose the web interface port
EXPOSE 8000

CMD ["python3", "-m", "emulation.controller"]
''')
    
    print(f"Created Controller Dockerfile: {dockerfile}")


def create_dynamics_image():
    """Create the dynamics simulator image Dockerfile"""
    dockerfile = DOCKER_DIR / 'dynamics' / 'Dockerfile'
    
    with open(dockerfile, 'w') as f:
        f.write('''FROM python:3.10-slim

# Install required packages
RUN apt-get update && apt-get install -y \\
    iputils-ping \\
    net-tools \\
    curl \\
    docker.io

# Install Python dependencies
COPY requirements-dynamics.txt /app/
RUN pip install -r /app/requirements-dynamics.txt

# Copy application code
COPY emulation /app/emulation/

# Set working directory
WORKDIR /app

# Create necessary directories
RUN mkdir -p /app/data /app/logs

CMD ["python3", "-m", "emulation.dynamics_service"]
''')
    
    print(f"Created Dynamics Dockerfile: {dockerfile}")


def create_requirements_files():
    """Create requirements files for each component"""
    # Node requirements
    with open(PROJECT_ROOT / 'requirements-node.txt', 'w') as f:
        f.write('''flask>=2.2.3
requests>=2.28.2
pymongo>=4.3.3
prometheus_client>=0.16.0
pydantic>=1.10.7
''')
    
    # Controller requirements
    with open(PROJECT_ROOT / 'requirements-controller.txt', 'w') as f:
        f.write('''fastapi>=0.95.0
uvicorn>=0.21.1
jinja2>=3.1.2
networkx>=2.6.3
pydantic>=1.10.7
docker>=6.0.1
pymongo>=4.3.3
plotly>=5.14.1
ipaddress>=1.0.23
requests>=2.28.2
''')
    
    # Dynamics requirements
    with open(PROJECT_ROOT / 'requirements-dynamics.txt', 'w') as f:
        f.write('''networkx>=2.6.3
skyfield>=1.45.1
pydantic>=1.10.7
docker>=6.0.1
pymongo>=4.3.3
requests>=2.28.2
''')
    
    print("Created requirements files")


def create_supervisord_conf():
    """Create the supervisor configuration file for node containers"""
    with open(DOCKER_DIR / 'supervisord.conf', 'w') as f:
        f.write('''[supervisord]
nodaemon=true
user=root
loglevel=info
logfile=/app/logs/supervisord.log
pidfile=/app/supervisord.pid

[program:frr]
command=/usr/lib/frr/frrinit.sh start
autostart=true
autorestart=true
startsecs=3
startretries=3
stdout_logfile=/app/logs/frr.log
stderr_logfile=/app/logs/frr-error.log

[program:node_agent]
command=python3 -m emulation.node_agent
autostart=true
autorestart=true
startsecs=5
startretries=3
stdout_logfile=/app/logs/node_agent.log
stderr_logfile=/app/logs/node_agent-error.log
''')
    
    print(f"Created supervisord.conf: {DOCKER_DIR / 'supervisord.conf'}")


def create_docker_compose():
    """Create the Docker Compose configuration file"""
    # Parse the network config to understand the topology
    parser = configparser.ConfigParser()
    parser.optionxform = str  # Keep case sensitivity in config file keys
    print(SCRIPT_DIR)
    print(PROJECT_ROOT)
    print(CONFIG_FILE)
    parser.read(CONFIG_FILE)
    
    # Extract basic network parameters
    num_rings = parser['network'].getint('rings', 4)
    num_routers = parser['network'].getint('routers', 4)
    use_ground_stations = parser['network'].getboolean('ground_stations', False)
    
    # Get ground station and vessel names if enabled, converting to lowercase
    ground_stations = []
    if use_ground_stations and 'ground_stations' in parser:
        ground_stations = [name.lower() for name in parser['ground_stations'].keys()]
    
    vessels = []
    if 'vessels' in parser:
        vessels = [name.lower() for name in parser['vessels'].keys()]
    
    # Create the docker-compose.yml file
    with open(PROJECT_ROOT / 'docker-compose.yml', 'w') as f:
        f.write('''version: '3.8'

services:
  # Controller service
  controller:
    build:
      context: .
      dockerfile: docker/controller/Dockerfile
    container_name: controller
    volumes:
      - ./emulation:/app/emulation
      - /var/run/docker.sock:/var/run/docker.sock
      - ./logs:/app/logs
      - ./data:/app/data
    ports:
      - "0.0.0.0:8000:8000"  # Bind to all interfaces for network access
    networks:
      - control_network
    environment:
      - CONFIG_FILE=/app/emulation/mnet/configs/small.net
    depends_on:
      - mongodb

  # Dynamics simulator service
  dynamics:
    build:
      context: .
      dockerfile: docker/dynamics/Dockerfile
    container_name: dynamics
    volumes:
      - ./emulation:/app/emulation
      - /var/run/docker.sock:/var/run/docker.sock
      - ./logs:/app/logs
      - ./data:/app/data
    networks:
      - control_network
    environment:
      - CONFIG_FILE=/app/emulation/mnet/configs/small.net
    depends_on:
      - controller
      
  # MongoDB for data storage
  mongodb:
    image: mongo:latest
    container_name: mongodb
    networks:
      - control_network
    volumes:
      - mongo_data:/data/db
    ports:
      - "27017:27017"
''')
        
        # Add satellite nodes (names converted to lowercase)
        f.write("  # Satellite nodes\n")
        for ring in range(num_rings):
            for node in range(num_routers):
                # Build the name and then convert to lowercase
                name = f"R{ring}_{node}"
                service_name = name.lower()
                f.write(f'''  {service_name}:
    build:
      context: .
      dockerfile: docker/node/Dockerfile
    container_name: {service_name}
    cap_add:
      - NET_ADMIN
      - SYS_ADMIN
    volumes:
      - ./logs/{service_name}:/app/logs
      - ./data/{service_name}:/app/data
    networks:
      - control_network
      - satellite_network
    environment:
      - NODE_NAME={service_name}
      - NODE_TYPE=satellite
      - CONTROLLER_URL=http://controller:8000
    depends_on:
      - controller

''')
        
        # Add ground stations (names converted to lowercase)
        if ground_stations:
            f.write("  # Ground stations\n")
            for name in ground_stations:
                service_name = name.lower()
                f.write(f'''  {service_name}:
    build:
      context: .
      dockerfile: docker/node/Dockerfile
    container_name: {service_name}
    cap_add:
      - NET_ADMIN
      - SYS_ADMIN
    volumes:
      - ./logs/{service_name}:/app/logs
      - ./data/{service_name}:/app/data
    networks:
      - control_network
      - ground_network
    environment:
      - NODE_NAME={service_name}
      - NODE_TYPE=ground_station
      - CONTROLLER_URL=http://controller:8000
    depends_on:
      - controller

''')
        
        # Add vessels (names converted to lowercase)
        if vessels:
            f.write("  # Vessels\n")
            for name in vessels:
                service_name = name.lower()
                f.write(f'''  {service_name}:
    build:
      context: .
      dockerfile: docker/node/Dockerfile
    container_name: {service_name}
    cap_add:
      - NET_ADMIN
      - SYS_ADMIN
    volumes:
      - ./logs/{service_name}:/app/logs
      - ./data/{service_name}:/app/data
    networks:
      - control_network
      - vessel_network
    environment:
      - NODE_NAME={service_name}
      - NODE_TYPE=vessel
      - CONTROLLER_URL=http://controller:8000
    depends_on:
      - controller

''')
        
        # Add networks and volumes
        f.write('''networks:
  # Control network for management traffic
  control_network:
    driver: bridge
    
  # Satellite network for inter-satellite links
  satellite_network:
    driver: bridge
    
  # Ground network for ground station connections
  ground_network:
    driver: bridge
    
  # Vessel network for vessel connections
  vessel_network:
    driver: bridge

volumes:
  mongo_data:
''')



def create_directory_structure():
    """Create log and data directories for each node"""
    # Parse the network config to understand the topology
    parser = configparser.ConfigParser()
    parser.optionxform = str  # Keep case sensitivity
    parser.read(CONFIG_FILE)
    
    # Extract basic network parameters
    num_rings = parser['network'].getint('rings', 4)
    num_routers = parser['network'].getint('routers', 4)
    
    # Get ground station and vessel names if available
    ground_stations = []
    if 'ground_stations' in parser:
        ground_stations = list(parser['ground_stations'].keys())
    
    vessels = []
    if 'vessels' in parser:
        vessels = list(parser['vessels'].keys())
    
    # Create directories for all nodes
    log_root = PROJECT_ROOT / 'logs'
    data_root = PROJECT_ROOT / 'data'
    
    # Create for satellites
    for ring in range(num_rings):
        for node in range(num_routers):
            name = f"R{ring}_{node}"
            (log_root / name).mkdir(exist_ok=True)
            (data_root / name).mkdir(exist_ok=True)
    
    # Create for ground stations
    for name in ground_stations:
        (log_root / name).mkdir(exist_ok=True)
        (data_root / name).mkdir(exist_ok=True)
    
    # Create for vessels
    for name in vessels:
        (log_root / name).mkdir(exist_ok=True)
        (data_root / name).mkdir(exist_ok=True)
    
    print(f"Created log and data directories for all nodes")


def copy_source_files():
    """Create necessary directories and init files, but don't overwrite existing content files"""
    # Create the path for required directories
    emulation_dir = PROJECT_ROOT / 'emulation'
    common_dir = emulation_dir / 'common'
    common_dir.mkdir(exist_ok=True)
    
    # Create a list of files to check
    required_files = [
        emulation_dir / 'node_agent.py',
        emulation_dir / 'controller.py',
        emulation_dir / 'dynamics_service.py'
    ]
    
    # Check if files exist, create placeholder only if missing
    for file_path in required_files:
        if not file_path.exists():
            print(f"Creating placeholder for {file_path.name} - you will need to add the actual content")
            with open(file_path, 'w') as f:
                f.write(f"# This is a placeholder for {file_path.name}\n# Replace with actual implementation\n")
        else:
            print(f"File {file_path.name} already exists, keeping existing content")
    
    # Create __init__.py files to make the modules importable (only if they don't exist)
    init_files = [
        emulation_dir / '__init__.py',
        common_dir / '__init__.py'
    ]
    
    for init_file in init_files:
        if not init_file.exists():
            with open(init_file, 'w') as f:
                f.write(f"# Python package initialization for {init_file.parent.name}\n")
            print(f"Created {init_file}")
        else:
            print(f"Init file {init_file} already exists, keeping existing content")
    
    print("Verified source files - existing files were not modified")


def main():
    """Main function to set up the Docker environment"""
    print("Setting up Docker environment for Satellite Network Simulator...")
    
    # Check if Docker is installed
    try:
        subprocess.run(["docker", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: Docker is not installed or not in PATH.")
        print("Please install Docker and Docker Compose before proceeding.")
        sys.exit(1)
    
    # Check if Docker Compose is installed
    try:
        subprocess.run(["docker-compose", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Warning: Docker Compose standalone binary not found.")
        print("This is fine if you're using Docker Compose as a Docker plugin.")
    
    # Create directories
    create_directories()
    
    # Create Dockerfiles
    create_node_image()
    create_controller_image()
    create_dynamics_image()
    
    # Create requirements files
    create_requirements_files()
    
    # Create supervisord.conf
    create_supervisord_conf()
    
    # Create Docker Compose file
    create_docker_compose()
    
    # Create directory structure for logs and data
    create_directory_structure()
    
    # Check source files
    copy_source_files()
    
    print("\nSetup completed successfully!")
    print("\nIMPORTANT NEXT STEPS:")
    print("1. Ensure your implementation files are in place:")
    print("   - emulation/controller.py - Main controller implementation")
    print("   - emulation/dynamics_service.py - Dynamics simulator")
    print("   - emulation/node_agent.py - Node agent for containers")
    print("2. Build and start the containers:")
    print("   $ docker-compose build")
    print("   $ docker-compose up -d")
    print("3. Check container logs if any issues:")
    print("   $ docker-compose logs controller")
    print("   $ docker-compose logs dynamics")
    print("4. Access the web interface at: http://localhost:8000")
    print("\nTo use a different configuration, edit:")
    print("   emulation/mnet/configs/small.net")
    print("And then rebuild your containers.")


if __name__ == "__main__":
    main()