'''
Controller - Main service that manages node containers and provides the web interface
'''

import os
import time
import datetime
import json
import logging
import threading
import ipaddress
from typing import Dict, List, Tuple, Any, Optional
import configparser
from pathlib import Path

import docker
import networkx as nx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import uvicorn
from pydantic import BaseModel
import requests
from pymongo import MongoClient

# Import your existing simulation modules
from emulation import torus_topo
from emulation import frr_config_topo
from emulation import simapi

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/app/logs/controller.log')
    ]
)
logger = logging.getLogger('controller')

# Initialize FastAPI app
app = FastAPI()

# Mount static files
app.mount("/static", StaticFiles(directory="/app/emulation/mnet/static"), name="static")

# Initialize Jinja2 templates
templates = Jinja2Templates(directory="/app/emulation/mnet/templates")

# Docker client
docker_client = docker.from_env()

# Connect to MongoDB
mongo_client = MongoClient('mongodb://mongodb:27017/')
db = mongo_client['satellite_network']
nodes_collection = db['nodes']
links_collection = db['links']
events_collection = db['events']
stats_collection = db['stats']

# Global state
topology_graph = nx.Graph()
satellite_positions = []
ground_station_positions = []
vessel_positions = []
satellite_links = []
ground_uplinks = []
registered_nodes = {}
events = []
start_time = time.time()

# Configuration
CONFIG_FILE = os.environ.get('CONFIG_FILE', '/app/emulation/mnet/configs/small.net')
DOCKER_NETWORK = os.environ.get('DOCKER_NETWORK', 'satnet')
BASE_SUBNET = os.environ.get('BASE_SUBNET', '10.1.0.0/16')
LOOPBACK_SUBNET = os.environ.get('LOOPBACK_SUBNET', '10.0.0.0/16')

# Network configuration
network_pool = ipaddress.IPv4Network(BASE_SUBNET)
loopback_pool = ipaddress.IPv4Network(LOOPBACK_SUBNET)
used_subnets = []


class NodeInfo(BaseModel):
    name: str
    type: str
    host: str


class NodeStatus(BaseModel):
    name: str
    type: str
    interfaces: Dict[str, Any]
    links: Dict[str, Any]
    uplinks: Optional[List[Dict[str, Any]]] = None
    position: Dict[str, float]
    running: bool


class LinkUpdate(BaseModel):
    node1_name: str
    node2_name: str
    up: bool
    delay: Optional[float] = None


def log_event(event_text: str):
    """Log an event to the database and in-memory list."""
    event_data = {
        'timestamp': time.time(),
        'event': event_text
    }
    events_collection.insert_one(event_data)
    events.append((time.strftime('%H:%M:%S'), event_text))
    
    # Keep only the last 100 events in memory
    if len(events) > 100:
        events.pop(0)
    
    logger.info(event_text)


def get_next_subnet(prefix_length: int = 24) -> ipaddress.IPv4Network:
    """Get the next available subnet from the pool."""
    for subnet in network_pool.subnets(new_prefix=prefix_length):
        if subnet not in used_subnets:
            used_subnets.append(subnet)
            return subnet
    raise ValueError("No available subnets left")


def get_next_loopback_ip() -> str:
    """Get the next available loopback IP address."""
    ips = list(loopback_pool.hosts())
    used_ips = [node.get('loopback_ip') for node in nodes_collection.find({}, {'loopback_ip': 1})]
    
    for ip in ips:
        ip_str = str(ip)
        if ip_str not in used_ips:
            return ip_str
    
    raise ValueError("No available loopback IPs left")


def create_node_container(node_name: str, node_type: str, config: Dict[str, Any]) -> str:
    """Create a Docker container for a network node."""
    # Check if container already exists (created by docker-compose)
    try:
        existing_container = docker_client.containers.get(node_name)
        print(f"Container {node_name} already exists, using existing container")
        
        # Store node information in database
        node_data = {
            'name': node_name,
            'type': node_type,
            'container_id': existing_container.id,
            'config': config,
            'created_at': time.time()
        }
        nodes_collection.insert_one(node_data)
        
        # Log event
        log_event(f"Using existing {node_type} node: {node_name}")
        
        return existing_container.id
    except docker.errors.NotFound:
        # Container doesn't exist, continue with creation
        pass
    
    # Get loopback IP
    loopback_ip = get_next_loopback_ip()
    
    # Configure environment variables
    environment = {
        'NODE_NAME': node_name,
        'NODE_TYPE': node_type,
        'CONTROLLER_URL': 'http://controller:8000',
        'LOOPBACK_IP': loopback_ip
    }
    
    # Add position information for relevant node types
    if node_type == 'satellite':
        environment.update({
            'INITIAL_LAT': str(config.get('latitude', 0)),
            'INITIAL_LON': str(config.get('longitude', 0)),
            'INITIAL_ALT': str(config.get('altitude', 0))
        })
    elif node_type in ['ground_station', 'vessel']:
        environment.update({
            'INITIAL_LAT': str(config.get('latitude', 0)),
            'INITIAL_LON': str(config.get('longitude', 0))
        })
    
    # Determine image name based on docker-compose project
    project_name = os.environ.get('COMPOSE_PROJECT_NAME', os.path.basename(os.getcwd().lower()))
    image_name = f"{project_name}_node"  # Use the same name as in docker-compose
    
    try:
        # Create the container
        container = docker_client.containers.run(
            image_name,
            name=node_name,
            detach=True,
            network=DOCKER_NETWORK,
            environment=environment,
            cap_add=['NET_ADMIN', 'SYS_ADMIN'],  # Needed for network configuration
            restart_policy={"Name": "unless-stopped"},
            volumes={
                f'/etc/frr/{node_name}': {'bind': '/etc/frr', 'mode': 'rw'},
                f'/var/log/frr/{node_name}': {'bind': '/var/log/frr', 'mode': 'rw'}
            }
        )
        
        # Store node information in database
        node_data = {
            'name': node_name,
            'type': node_type,
            'container_id': container.id,
            'loopback_ip': loopback_ip,
            'config': config,
            'created_at': time.time()
        }
        nodes_collection.insert_one(node_data)
        
        # Log event
        log_event(f"Created {node_type} node: {node_name}")
        
        return container.id
    except Exception as e:
        log_event(f"Error creating node {node_name}: {str(e)}")
        logger.error(f"Error creating node {node_name}: {e}")
        return None


def configure_frr(node_name: str, config_files: Dict[str, str]):
    """Configure FRR on a node."""
    try:
        # Check if node exists
        node = nodes_collection.find_one({'name': node_name})
        if not node:
            logger.error(f"Node {node_name} not found")
            return False
        
        # Send configuration request to node agent
        response = requests.post(
            f"http://{node_name}:5000/config/frr",
            json={'config_files': config_files},
            timeout=5
        )
        
        if response.status_code == 200 and response.json().get('success'):
            logger.info(f"Successfully configured FRR on {node_name}")
            return True
        else:
            logger.error(f"Failed to configure FRR on {node_name}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error configuring FRR on {node_name}: {e}")
        return False


def setup_link(node1: str, node2: str, subnet: ipaddress.IPv4Network, delay: float = 1.0) -> bool:
    """Set up a link between two nodes."""
    try:
        # Get host IPs from the subnet
        hosts = list(subnet.hosts())
        if len(hosts) < 2:
            logger.error(f"Subnet {subnet} does not have enough addresses")
            return False
            
        node1_ip = str(hosts[0])
        node2_ip = str(hosts[1])
        netmask = subnet.prefixlen
        
        # Create virtual interfaces for the link
        interface1 = f"{node1}-to-{node2}"
        interface2 = f"{node2}-to-{node1}"
        
        # Configure node1
        response1 = requests.post(
            f"http://{node1}:5000/config/interface",
            json={
                'name': interface1,
                'ip_address': node1_ip,
                'netmask': str(netmask)
            },
            timeout=5
        )
        
        if not (response1.status_code == 200 and response1.json().get('success')):
            logger.error(f"Failed to configure interface on {node1}: {response1.text}")
            return False
            
        # Configure node2
        response2 = requests.post(
            f"http://{node2}:5000/config/interface",
            json={
                'name': interface2,
                'ip_address': node2_ip,
                'netmask': str(netmask)
            },
            timeout=5
        )
        
        if not (response2.status_code == 200 and response2.json().get('success')):
            logger.error(f"Failed to configure interface on {node2}: {response2.text}")
            return False
        
        # Configure link on node1
        response3 = requests.post(
            f"http://{node1}:5000/config/link",
            json={
                'neighbor': node2,
                'local_ip': node1_ip,
                'remote_ip': node2_ip,
                'interface': interface1,
                'delay': delay
            },
            timeout=5
        )
        
        if not (response3.status_code == 200 and response3.json().get('success')):
            logger.error(f"Failed to configure link on {node1}: {response3.text}")
            return False
            
        # Configure link on node2
        response4 = requests.post(
            f"http://{node2}:5000/config/link",
            json={
                'neighbor': node1,
                'local_ip': node2_ip,
                'remote_ip': node1_ip,
                'interface': interface2,
                'delay': delay
            },
            timeout=5
        )
        
        if not (response4.status_code == 200 and response4.json().get('success')):
            logger.error(f"Failed to configure link on {node2}: {response4.text}")
            return False
        
        # Store link information in database
        link_data = {
            'node1': node1,
            'node2': node2,
            'subnet': str(subnet),
            'node1_ip': node1_ip,
            'node2_ip': node2_ip,
            'interface1': interface1,
            'interface2': interface2,
            'delay': delay,
            'status': 'up',
            'created_at': time.time()
        }
        links_collection.insert_one(link_data)
        
        # Log event
        log_event(f"Created link between {node1} and {node2}")
        
        return True
    except Exception as e:
        logger.error(f"Error setting up link between {node1} and {node2}: {e}")
        return False


def setup_uplink(ground_station: str, satellite: str, subnet: ipaddress.IPv4Network, 
                distance: int, delay: float, default: bool = False) -> bool:
    """Set up an uplink between a ground station and a satellite."""
    try:
        # Get host IPs from the subnet
        hosts = list(subnet.hosts())
        if len(hosts) < 2:
            logger.error(f"Subnet {subnet} does not have enough addresses")
            return False
            
        ground_ip = str(hosts[0])
        satellite_ip = str(hosts[1])
        netmask = subnet.prefixlen
        
        # Create interface names
        ground_intf = f"{ground_station}-to-{satellite}"
        satellite_intf = f"{satellite}-to-{ground_station}"
        
        # Configure ground station interface
        response1 = requests.post(
            f"http://{ground_station}:5000/config/interface",
            json={
                'name': ground_intf,
                'ip_address': ground_ip,
                'netmask': str(netmask)
            },
            timeout=5
        )
        
        if not (response1.status_code == 200 and response1.json().get('success')):
            logger.error(f"Failed to configure interface on {ground_station}: {response1.text}")
            return False
            
        # Configure satellite interface
        response2 = requests.post(
            f"http://{satellite}:5000/config/interface",
            json={
                'name': satellite_intf,
                'ip_address': satellite_ip,
                'netmask': str(netmask)
            },
            timeout=5
        )
        
        if not (response2.status_code == 200 and response2.json().get('success')):
            logger.error(f"Failed to configure interface on {satellite}: {response2.text}")
            return False
        
        # Configure uplink on ground station
        response3 = requests.post(
            f"http://{ground_station}:5000/config/uplink",
            json={
                'satellite': satellite,
                'local_ip': ground_ip,
                'remote_ip': satellite_ip,
                'interface': ground_intf,
                'distance': distance,
                'delay': delay,
                'default': default
            },
            timeout=5
        )
        
        if not (response3.status_code == 200 and response3.json().get('success')):
            logger.error(f"Failed to configure uplink on {ground_station}: {response3.text}")
            return False
            
        # Configure link on satellite (not as an uplink, but as a regular link)
        response4 = requests.post(
            f"http://{satellite}:5000/config/link",
            json={
                'neighbor': ground_station,
                'local_ip': satellite_ip,
                'remote_ip': ground_ip,
                'interface': satellite_intf,
                'delay': delay
            },
            timeout=5
        )
        
        if not (response4.status_code == 200 and response4.json().get('success')):
            logger.error(f"Failed to configure link on {satellite}: {response4.text}")
            return False
        
        # Store uplink information in database
        uplink_data = {
            'ground_station': ground_station,
            'satellite': satellite,
            'subnet': str(subnet),
            'ground_ip': ground_ip,
            'satellite_ip': satellite_ip,
            'ground_interface': ground_intf,
            'satellite_interface': satellite_intf,
            'distance': distance,
            'delay': delay,
            'default': default,
            'status': 'up',
            'created_at': time.time()
        }
        links_collection.insert_one(uplink_data)
        
        # Log event
        log_event(f"Created uplink from {ground_station} to {satellite}")
        
        return True
    except Exception as e:
        logger.error(f"Error setting up uplink between {ground_station} and {satellite}: {e}")
        return False


def update_link_state(node1: str, node2: str, up: bool, delay: Optional[float] = None) -> bool:
    """Update the state of a link between two nodes."""
    try:
        # Find the link in the database
        link = links_collection.find_one({
            '$or': [
                {'node1': node1, 'node2': node2},
                {'node1': node2, 'node2': node1}
            ]
        })
        
        if not link:
            logger.warning(f"Link between {node1} and {node2} not found - will be created at next provision")
            return False
        
        # Update TC rules to change link state on both nodes
        for node, remote, interface in [(link['node1'], link['node2'], link['interface1']),
                                     (link['node2'], link['node1'], link['interface2'])]:
            if not up:
                # Take the interface down
                response = requests.post(
                    f"http://{node}:5000/config/interface",
                    json={
                        'name': interface,
                        'status': 'down'
                    },
                    timeout=5
                )
            else:
                # Bring the interface up
                response = requests.post(
                    f"http://{node}:5000/config/interface",
                    json={
                        'name': interface,
                        'status': 'up'
                    },
                    timeout=5
                )
            
            if not (response.status_code == 200 and response.json().get('success')):
                logger.error(f"Failed to update interface state on {node}: {response.text}")
                return False
            
            # Update delay if specified
            if delay is not None:
                response = requests.post(
                    f"http://{node}:5000/config/link",
                    json={
                        'neighbor': remote,
                        'delay': delay
                    },
                    timeout=5
                )
                
                if not (response.status_code == 200 and response.json().get('success')):
                    logger.error(f"Failed to update link delay on {node}: {response.text}")
                    return False
        
        # Update link status in database
        new_status = 'up' if up else 'down'
        links_collection.update_one(
            {'_id': link['_id']},
            {'$set': {'status': new_status}}
        )
        
        if delay is not None:
            links_collection.update_one(
                {'_id': link['_id']},
                {'$set': {'delay': delay}}
            )
        
        # Log event
        if delay is not None:
            log_event(f"Updated link between {node1} and {node2} - status: {new_status}, delay: {delay}ms")
        else:
            log_event(f"Updated link between {node1} and {node2} - status: {new_status}")
        
        return True
    except Exception as e:
        logger.error(f"Error updating link state between {node1} and {node2}: {e}")
        return False


def load_network_from_config(config_file: str) -> bool:
    """Initialize the network from a configuration file."""
    try:
        # Parse the configuration file
        parser = configparser.ConfigParser()
        parser.optionxform = str  # Keep case sensitivity
        parser.read(config_file)
        
        # Extract network parameters
        num_rings = parser['network'].getint('rings', 4)
        num_routers = parser['network'].getint('routers', 4)
        use_ground_stations = parser['network'].getboolean('ground_stations', False)
        
        # Extract physical parameters
        inclination = parser.getfloat('constellation', 'inclination', fallback=53.9)
        altitude = parser.getfloat('constellation', 'altitude', fallback=550)
        
        # Create NetworkX graph
        global topology_graph
        topology_graph = torus_topo.create_network(
            num_rings=num_rings,
            num_ring_nodes=num_routers,
            ground_stations=use_ground_stations,
            inclination=inclination,
            altitude=altitude
        )
        
        # Add ground stations if enabled
        ground_station_data = {}
        if use_ground_stations and 'ground_stations' in parser:
            for name, coords in parser['ground_stations'].items():
                lat, lon = map(float, coords.split(','))
                ground_station_data[name] = (lat, lon)
                
        # Add vessels if present
        vessel_data = {}
        if 'vessels' in parser:
            for name, waypoint_str in parser['vessels'].items():
                waypoints = []
                for waypoint in waypoint_str.split(';'):
                    lat, lon = map(float, waypoint.split(','))
                    waypoints.append((lat, lon))
                vessel_data[name] = waypoints
        
        # Annotate the graph with IP addresses
        frr_config_topo.annotate_graph(topology_graph)
        
        log_event(f"Loaded network configuration: {num_rings} rings, {num_routers} routers per ring")
        return True
        
    except Exception as e:
        logger.error(f"Error loading network configuration: {e}")
        return False


def provision_network():
    """Register Docker containers created by Docker Compose and configure the network topology."""
    try:
        # Create Docker network if it doesn't exist
        try:
            network = docker_client.networks.get(DOCKER_NETWORK)
        except docker.errors.NotFound:
            network = docker_client.networks.create(
                DOCKER_NETWORK,
                driver="bridge",
                ipam=docker.types.IPAMConfig(
                    pool_configs=[docker.types.IPAMPool(subnet=BASE_SUBNET)]
                )
            )
            log_event(f"Created Docker network: {DOCKER_NETWORK}")
        
        # Register all existing containers
        # For each type of node, check if it exists and register it
        all_nodes = []
        all_nodes.extend(torus_topo.satellites(topology_graph))
        all_nodes.extend(torus_topo.ground_stations(topology_graph))
        all_nodes.extend(torus_topo.vessels(topology_graph))
        
        for name in all_nodes:
            try:
                # Check if container exists (using lowercase container names)
                container = docker_client.containers.get(name.lower())
                
                # Container exists, register it
                node_type = "satellite"
                if name in torus_topo.ground_stations(topology_graph):
                    node_type = "ground_station"
                elif name in torus_topo.vessels(topology_graph):
                    node_type = "vessel"
                
                # Extract config for this node
                node = topology_graph.nodes[name]
                config = {}
                
                if node_type == "satellite":
                    orbit = node.get('orbit')
                    if orbit:
                        config = {
                            'altitude': orbit.altitude,
                            'inclination': orbit.inclination,
                            'right_ascension': orbit.right_ascension,
                            'mean_anomaly': orbit.mean_anomaly
                        }
                    else:
                        config = {
                            'altitude': node.get('altitude', 550),
                            'inclination': 0,
                            'right_ascension': 0,
                            'mean_anomaly': 0
                        }
                elif node_type in ["ground_station", "vessel"]:
                    config = {
                        'latitude': node.get(torus_topo.LAT, 0),
                        'longitude': node.get(torus_topo.LON, 0)
                    }
                    if node_type == "vessel" and "waypoints" in node:
                        config['waypoints'] = node.get('waypoints', [])
                
                # Get loopback IP if needed for this node
                loopback_ip = get_next_loopback_ip()
                
                # Store node information in database
                node_data = {
                    'name': name,
                    'type': node_type,
                    'container_id': container.id,
                    'loopback_ip': loopback_ip,
                    'config': config,
                    'created_at': time.time()
                }
                nodes_collection.insert_one(node_data)
                
                log_event(f"Registered existing {node_type} node: {name}")
            except docker.errors.NotFound:
                # Container doesn't exist, which is unexpected since Docker Compose should create all containers
                log_event(f"Warning: Container {name.lower()} not found but expected from Docker Compose")
        
        # Wait for containers to be ready
        log_event("Waiting for all containers to register...")
        time.sleep(5)  # Give containers some time to start up
        
        # Create links between satellites
        for edge in topology_graph.edges:
            node1, node2 = edge
            edge_data = topology_graph.edges[edge]
            
            # Skip if not both satellites
            if (node1 not in torus_topo.satellites(topology_graph) or 
                node2 not in torus_topo.satellites(topology_graph)):
                continue
                
            # Get subnet for the link
            subnet = get_next_subnet(30)  # /30 for point-to-point
            
            # Set up the link
            delay = edge_data.get('delay', 1.0)
            try:
                setup_link(node1, node2, subnet, delay)
            except Exception as e:
                logger.warning(f"Could not set up link between {node1} and {node2}: {e}")
                log_event(f"Warning: Link setup between {node1} and {node2} failed")
        
        # Create uplinks between ground stations and satellites
        min_elevation = parser['physical'].getint('min_elevation', 10)
        
        # For now, just create initial placeholder uplinks
        # The dynamics simulator will update these based on satellite positions
        for gs_name in torus_topo.ground_stations(topology_graph):
            # Find the closest satellite for initial connection
            satellites_list = list(torus_topo.satellites(topology_graph))
            if satellites_list:
                closest_sat = satellites_list[0]  # Just use first satellite for now
                
                # Get subnet for the uplink
                subnet = get_next_subnet(30)  # /30 for point-to-point
                
                # Set up the uplink with default parameters
                try:
                    setup_uplink(gs_name, closest_sat, subnet, distance=1000, delay=10.0, default=True)
                except Exception as e:
                    logger.warning(f"Could not set up initial uplink from {gs_name} to {closest_sat}: {e}")
                    log_event(f"Warning: Initial uplink from {gs_name} to {closest_sat} failed")
        
        # Create uplinks for vessels similar to ground stations
        for vessel_name in torus_topo.vessels(topology_graph):
            # Find the closest satellite for initial connection
            satellites_list = list(torus_topo.satellites(topology_graph))
            if satellites_list:
                closest_sat = satellites_list[0]  # Just use first satellite for now
                
                # Get subnet for the uplink
                subnet = get_next_subnet(30)  # /30 for point-to-point
                
                # Set up the uplink with default parameters
                try:
                    setup_uplink(vessel_name, closest_sat, subnet, distance=1000, delay=10.0, default=True)
                except Exception as e:
                    logger.warning(f"Could not set up initial uplink from {vessel_name} to {closest_sat}: {e}")
                    log_event(f"Warning: Initial uplink from {vessel_name} to {closest_sat} failed")
        
        log_event("Network provisioning completed")
        return True
    except Exception as e:
        logger.error(f"Error provisioning network: {e}")
        return False


@app.get("/")
async def root(request: Request):
    """Render the main dashboard."""
    # Get link statistics
    link_stats = {
        "count": links_collection.count_documents({}),
        "up_count": links_collection.count_documents({"status": "up"})
    }
    
    # Get router list
    routers = []
    for name in torus_topo.satellites(topology_graph):
        node = topology_graph.nodes[name]
        ip = ""
        if node.get("ip") is not None:
            ip = format(node.get("ip"))
        routers.append((name, ip))
    
    # Create stations list as proper objects with a name attribute
    stations = []
    for name in registered_nodes:
        if registered_nodes[name].get('type') in ['ground_station', 'vessel']:
            # Create a station-like object with necessary attributes
            class StationInfo:
                def __init__(self, station_name, ip=""):
                    self.name = station_name
                    self.ip = ip
                
                def defaultIP(self):
                    return self.ip
                
            station_obj = StationInfo(name)
            stations.append(station_obj)
    
    # Get events
    recent_events = list(events_collection.find().sort("timestamp", -1).limit(10))
    formatted_events = []
    for event in recent_events:
        time_str = datetime.datetime.fromtimestamp(event['timestamp']).strftime('%H:%M:%S')
        formatted_events.append((time_str, event['event']))
    
    # Create ping stats
    ping_stats = {}
    for name in registered_nodes:
        ping_stats[name] = []  # Initialize with empty list
    
    network_info = {
        "rings": topology_graph.graph.get("rings", 0),
        "ring_nodes": topology_graph.graph.get("ring_nodes", 0),
        "current_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "run_time": str(datetime.timedelta(seconds=int(time.time() - start_time))),
        "events": formatted_events,
        "routers": routers,
        "stations": stations,
        "link_stats": link_stats,
        "ping_stats": ping_stats,
        "monitor_stable_nodes": True,
        "stats_dates": [],          # Add empty arrays for charts
        "stats_stable_ok": [],
        "stats_stable_fail": [],
        "stats_dynamic_ok": [],
        "stats_dynamic_fail": []
    }
    
    return templates.TemplateResponse("main.html", {"request": request, "info": network_info})


@app.get("/positions")
async def get_positions():
    """Return current positions of all nodes."""
    return {
        "satellites": satellite_positions,
        "ground_stations": ground_station_positions,
        "vessels": vessel_positions,
        "satellite_links": satellite_links,
        "ground_uplinks": ground_uplinks
    }


@app.put("/positions")
async def update_positions(positions: simapi.GraphData):
    """Update positions of nodes and link states."""
    global satellite_positions, ground_station_positions, vessel_positions
    global satellite_links, ground_uplinks
    
    # Update stored positions
    satellite_positions = positions.satellites
    ground_station_positions = positions.ground_stations
    vessel_positions = positions.vessels
    satellite_links = positions.satellite_links
    ground_uplinks = positions.ground_uplinks
    
    # Update node positions in the database
    for sat in positions.satellites:
        nodes_collection.update_one(
            {'name': sat.name},
            {'$set': {
                'position': {
                    'lat': sat.lat,
                    'lon': sat.lon,
                    'alt': sat.height
                }
            }}
        )
        
        # Send position update to node
        try:
            requests.post(
                f"http://{sat.name}:5000/config/position",
                json={
                    'lat': sat.lat,
                    'lon': sat.lon,
                    'alt': sat.height
                },
                timeout=2
            )
        except Exception:
            pass
    
    for gs in positions.ground_stations:
        nodes_collection.update_one(
            {'name': gs.name},
            {'$set': {
                'position': {
                    'lat': gs.lat,
                    'lon': gs.lon
                }
            }}
        )
        
        # Send position update to node
        try:
            requests.post(
                f"http://{gs.name}:5000/config/position",
                json={
                    'lat': gs.lat,
                    'lon': gs.lon
                },
                timeout=2
            )
        except Exception:
            pass
    
    for vessel in positions.vessels:
        nodes_collection.update_one(
            {'name': vessel.name},
            {'$set': {
                'position': {
                    'lat': vessel.lat,
                    'lon': vessel.lon
                }
            }}
        )
        
        # Send position update to node
        try:
            requests.post(
                f"http://{vessel.name}:5000/config/position",
                json={
                    'lat': vessel.lat,
                    'lon': vessel.lon
                },
                timeout=2
            )
        except Exception:
            pass
    
    # Update satellite links
    for link in positions.satellite_links:
        update_link_state(link.node1_name, link.node2_name, link.up, link.delay)
    
    # Update ground uplinks
    for uplink_group in positions.ground_uplinks:
        ground_node = uplink_group.ground_node
        
        # Find all previous uplinks for this station
        previous_uplinks = links_collection.find({
            '$or': [
                {'ground_station': ground_node},
                {'node1': ground_node, 'node2': {'$regex': '^R'}},
                {'node1': {'$regex': '^R'}, 'node2': ground_node}
            ]
        })
        
        current_uplinks = {uplink.sat_node for uplink in uplink_group.uplinks}
        
        # Remove uplinks that are no longer valid
        for old_uplink in previous_uplinks:
            sat_node = old_uplink.get('satellite') or (
                old_uplink['node2'] if old_uplink['node1'] == ground_node else old_uplink['node1']
            )
            
            if sat_node not in current_uplinks:
                # Remove this uplink
                # (In production, we might want to just mark it down rather than remove)
                links_collection.delete_one({'_id': old_uplink['_id']})
        
        # Set up new uplinks and update existing ones
        for uplink in uplink_group.uplinks:
            # Check if uplink already exists
            existing = links_collection.find_one({
                '$or': [
                    {'ground_station': ground_node, 'satellite': uplink.sat_node},
                    {'node1': ground_node, 'node2': uplink.sat_node},
                    {'node1': uplink.sat_node, 'node2': ground_node}
                ]
            })
            
            if existing:
                # Update existing uplink
                delay = calculate_link_delay(uplink.distance)
                update_link_state(ground_node, uplink.sat_node, True, delay)
            else:
                # Create new uplink
                subnet = get_next_subnet(30)
                delay = calculate_link_delay(uplink.distance)
                setup_uplink(ground_node, uplink.sat_node, subnet, uplink.distance, delay)
    
    return {"status": "OK"}


@app.put("/link")
async def set_link(link: LinkUpdate):
    """Set link state between two nodes."""
    success = update_link_state(link.node1_name, link.node2_name, link.up, link.delay)
    if success:
        return {"status": "OK"}
    else:
        raise HTTPException(status_code=400, detail="Failed to update link state")


@app.post("/api/node/register")
async def register_node(node_info: NodeInfo):
    """Register a node with the controller."""
    # Use the name as provided, but store with lowercase for consistency
    node_name = node_info.name
    registered_nodes[node_name] = {
        'type': node_info.type,
        'host': node_info.host,
        'last_seen': time.time()
    }
    
    log_event(f"Node registered: {node_name} ({node_info.type})")
    return {"status": "OK"}


@app.post("/api/node/status")
async def update_node_status(status: NodeStatus):
    """Update node status in the controller."""
    node_name = status.name
    
    # Check if node exists, if not register it automatically
    if node_name not in registered_nodes:
        registered_nodes[node_name] = {
            'type': status.type,
            'host': 'auto-registered',
            'last_seen': time.time()
        }
        log_event(f"Auto-registered node: {node_name} ({status.type})")
    
    # Store the latest status
    registered_nodes[node_name]['last_seen'] = time.time()
    registered_nodes[node_name]['status'] = status.dict()

@app.get("/view/router/{node}", response_class=HTMLResponse)
def view_router(request: Request, node: str):
    """View details of a specific router."""
    router_info = {
        "name": node,
        "ip": None,
        "neighbors": {},
        "lat": None,
        "lon": None,
        "height": None
    }
    
    # Get router information from database or registered_nodes
    node_data = registered_nodes.get(node, {})
    if 'status' in node_data:
        status = node_data['status']
        router_info["ip"] = status.get("interfaces", {}).get("loopback", {}).get("ip")
        router_info["neighbors"] = status.get("links", {})
        
        # Get position data
        if 'position' in status:
            router_info["lat"] = status["position"].get("lat")
            router_info["lon"] = status["position"].get("lon")
            router_info["height"] = status["position"].get("alt")
    
    # Get status information
    status_list = {}
    for name in registered_nodes:
        # Use a simplified status: 1 = up, 0 = down, None = unknown
        status_val = None
        if name in node_data.get('status', {}).get('links', {}):
            link_status = node_data['status']['links'][name].get('status', 'unknown')
            status_val = 1 if link_status == 'up' else 0
        status_list[name] = status_val
    
    # Get ring list for display
    ring_list = topology_graph.graph.get("ring_list", [])
    
    return templates.TemplateResponse(
        name="router.html",
        context={
            "request": request,
            "router": router_info,
            "ring_list": ring_list,
            "status_list": status_list,
        },
    )


@app.get("/view/station/{name}", response_class=HTMLResponse)
def view_station(request: Request, name: str):
    """View details of a specific ground station or vessel."""
    station = {
        "name": name,
        "uplinks": []
    }
    
    # Get station information from database or registered_nodes
    node_data = registered_nodes.get(name, {})
    if 'status' in node_data:
        status = node_data['status']
        station["uplinks"] = status.get("uplinks", [])
    
    # Get status information (similar to view_router)
    status_list = {}
    for node_name in registered_nodes:
        if node_name.startswith('R'):  # Satellite
            # Default to unknown status
            status_val = None
            
            # Check if we have uplink information
            for uplink in station.get("uplinks", []):
                if uplink.get("satellite") == node_name:
                    status_val = 1  # Connected
                    break
            
            status_list[node_name] = status_val
    
    # Get ring list for display
    ring_list = topology_graph.graph.get("ring_list", [])
    
    return templates.TemplateResponse(
        request=request,
        name="station.html",
        context={"station": station, "ring_list": ring_list, "status_list": status_list}
    )

def calculate_link_delay(distance_km: float) -> float:
    '''
    Calculate link delay based on distance.
    
    Args:
        distance_km: Distance in kilometers between nodes
        
    Returns:
        Delay in milliseconds
    '''
    SPEED_OF_LIGHT = 299792.458  # km/s
    PROCESSING_DELAY = 1  # ms (fixed component for equipment/processing)
    
    # Calculate propagation delay (distance/speed)
    prop_delay = (distance_km / SPEED_OF_LIGHT) * 1000  # Convert to ms
    
    # Add fixed processing delay
    total_delay = prop_delay + PROCESSING_DELAY
    
    return round(total_delay, 3)  # Round to 3 decimal places


def start_background_tasks():
    """Start background tasks for maintenance and monitoring."""
    def monitor_nodes():
        """Periodically check node status and collect statistics."""
        while True:
            current_time = time.time()
            
            # Check for inactive nodes
            inactive_nodes = []
            for name, data in registered_nodes.items():
                if current_time - data.get('last_seen', 0) > 60:  # 1 minute timeout
                    inactive_nodes.append(name)
            
            # Log inactive nodes
            if inactive_nodes:
                log_event(f"Inactive nodes detected: {', '.join(inactive_nodes)}")
            
            # Collect overall statistics
            stats = {
                'timestamp': current_time,
                'total_nodes': len(registered_nodes),
                'active_nodes': len(registered_nodes) - len(inactive_nodes),
                'satellite_count': len([n for n, d in registered_nodes.items() 
                                     if d.get('type') == 'satellite']),
                'ground_station_count': len([n for n, d in registered_nodes.items() 
                                          if d.get('type') == 'ground_station']),
                'vessel_count': len([n for n, d in registered_nodes.items() 
                                  if d.get('type') == 'vessel'])
            }
            
            stats_collection.insert_one(stats)
            
            time.sleep(30)
    
    # Start the monitoring thread
    monitor_thread = threading.Thread(target=monitor_nodes, daemon=True)
    monitor_thread.start()


@app.on_event("startup")
async def startup_event():
    """Initialize the controller on startup."""
    # Load network configuration
    load_network_from_config(CONFIG_FILE)
    
    # Provision the network
    provision_network()
    
    # Start background tasks
    start_background_tasks()


if __name__ == "__main__":
    # Run the API server
    uvicorn.run("emulation.controller:app", host="0.0.0.0", port=8000, reload=False)