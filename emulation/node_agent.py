'''
Node Agent - Runs on each satellite, ground station, or vessel container
Responds to configuration requests and reports status to the controller
'''

try:
    import os
    import sys
    print(f"Python version: {sys.version}")
    print(f"PYTHONPATH: {os.environ.get('PYTHONPATH', 'not set')}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Directory contents: {os.listdir('.')}")
    print(f"Emulation directory: {os.listdir('/app/emulation')}")
    
    # Rest of imports...
except Exception as e:
    with open('/app/logs/startup-error.log', 'w') as f:
        f.write(f"Error during startup: {str(e)}\n")
    sys.exit(1)

import os
import time
import json
import socket
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union, Any
import threading

from flask import Flask, request, jsonify
import requests
from prometheus_client import start_http_server, Gauge, Counter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/app/logs/node_agent.log')
    ]
)
logger = logging.getLogger('node_agent')

# Initialize Flask app
app = Flask(__name__)

# Initialize Prometheus metrics
NODE_TYPE = os.environ.get('NODE_TYPE', 'unknown')
NODE_NAME = os.environ.get('NODE_NAME', socket.gethostname())
CONTROLLER_URL = os.environ.get('CONTROLLER_URL', 'http://controller:8000')

# Prometheus metrics
ping_latency = Gauge('ping_latency_ms', 'Ping latency in milliseconds', ['target'])
link_status = Gauge('link_status', 'Link status (1=up, 0=down)', ['neighbor'])
frr_status = Gauge('frr_status', 'FRR service status (1=up, 0=down)', ['service'])
ping_success_counter = Counter('ping_success_total', 'Total successful pings', ['target'])
ping_failure_counter = Counter('ping_failure_total', 'Total failed pings', ['target'])

# Node state
node_config = {
    'name': NODE_NAME,
    'type': NODE_TYPE,
    'interfaces': {},
    'links': {},
    'uplinks': [] if NODE_TYPE == 'ground_station' or NODE_TYPE == 'vessel' else None,
    'position': {'lat': 0, 'lon': 0, 'alt': 0},
    'running': True
}


def enforce_network_isolation():
    """Set up iptables rules to enforce satellite-only routing"""
    if NODE_TYPE not in ['ground_station', 'vessel']:
        return True
        
    try:
        # Clear any existing rules first to avoid duplication
        subprocess.run(['iptables', '-F', 'FORWARD'], check=False)
        
        # Block direct communication between ground stations
        subprocess.run([
            'iptables', '-A', 'FORWARD', '-d', '172.20.0.0/16', '-j', 'DROP'
        ], check=False)
        # Block direct communication with vessels
        subprocess.run([
            'iptables', '-A', 'FORWARD', '-d', '172.21.0.0/16', '-j', 'DROP'
        ], check=False)
        
        # Allow traffic to satellite network
        subprocess.run([
            'iptables', '-A', 'FORWARD', '-d', '172.19.0.0/16', '-j', 'ACCEPT'
        ], check=False)
        
        # Allow traffic to own uplinks
        for uplink in node_config['uplinks']:
            satellite_ip = uplink['remote_ip']
            subprocess.run([
                'iptables', '-A', 'FORWARD', '-d', satellite_ip, '-j', 'ACCEPT'
            ], check=False)
        
        logger.info(f"Network isolation rules applied for {NODE_NAME}")
        return True
    except Exception as e:
        logger.error(f"Failed to set network isolation: {e}")
        return False

def update_frr_config(config_files: Dict[str, str]) -> bool:
    """Update FRR configuration files."""
    try:
        for filename, content in config_files.items():
            config_path = Path(f'/etc/frr/{filename}')
            config_path.write_text(content)
        
        # Apply new configuration by reloading FRR
        subprocess.run(['/usr/lib/frr/frrinit.sh', 'reload'], check=True)
        return True
    except Exception as e:
        logger.error(f"Failed to update FRR config: {e}")
        return False


def configure_interface(name: str, ip_address: str, netmask: str) -> bool:
    """Configure a network interface with the given IP."""
    try:
        # Ensure the interface exists first (may be a virtual interface)
        if not Path(f'/sys/class/net/{name}').exists():
            subprocess.run(['ip', 'link', 'add', name, 'type', 'dummy'], check=True)
            subprocess.run(['ip', 'link', 'set', name, 'up'], check=True)
        
        # Configure the IP address
        subprocess.run(['ip', 'addr', 'add', f'{ip_address}/{netmask}', 'dev', name], check=True)
        
        # Update node state
        node_config['interfaces'][name] = {
            'ip': ip_address,
            'netmask': netmask,
            'status': 'up'
        }
        return True
    except Exception as e:
        logger.error(f"Failed to configure interface {name}: {e}")
        return False


def monitor_links():
    """Periodically monitor link status and report metrics."""
    # Apply network isolation at startup
    enforce_network_isolation()
    while node_config['running']:
        for neighbor, link_info in node_config['links'].items():
            # Ping the neighbor to check link status
            try:
                result = subprocess.run(
                    ['ping', '-c', '1', '-W', '1', link_info['remote_ip']], 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                if result.returncode == 0:
                    # Extract ping time (in ms)
                    try:
                        time_str = result.stdout.split('time=')[1].split(' ms')[0]
                        latency = float(time_str)
                    except (IndexError, ValueError):
                        latency = 0
                    
                    ping_latency.labels(target=neighbor).set(latency)
                    link_status.labels(neighbor=neighbor).set(1)
                    ping_success_counter.labels(target=neighbor).inc()
                else:
                    link_status.labels(neighbor=neighbor).set(0)
                    ping_failure_counter.labels(target=neighbor).inc()
            except Exception as e:
                logger.error(f"Error monitoring link to {neighbor}: {e}")
                link_status.labels(neighbor=neighbor).set(0)
        
        # Check FRR status
        for service in ['zebra', 'ospfd', 'staticd']:
            try:
                # Check if process is running by looking for PID files
                pid_file = f"/var/run/frr/{service}.pid"
                if os.path.exists(pid_file):
                    with open(pid_file, 'r') as f:
                        pid = f.read().strip()
                        # Check if process with this PID exists
                        try:
                            os.kill(int(pid), 0)  # Signal 0 doesn't kill but checks if process exists
                            frr_status.labels(service=service).set(1)
                        except (OSError, ProcessLookupError):
                            frr_status.labels(service=service).set(0)
                else:
                    # Alternative: Check with pgrep
                    try:
                        subprocess.run(['pgrep', service], check=True, stdout=subprocess.PIPE)
                        frr_status.labels(service=service).set(1)
                    except subprocess.CalledProcessError:
                        frr_status.labels(service=service).set(0)
            except Exception as e:
                logger.error(f"Error checking FRR service {service}: {e}")
                frr_status.labels(service=service).set(0)
                
        # Report status to controller
        try:
            requests.post(
                f"{CONTROLLER_URL}/api/node/status",
                json=node_config
            )
        except Exception as e:
            logger.error(f"Failed to report status to controller: {e}")
            
        time.sleep(10)  # Wait 10 seconds before checking again


@app.route('/config/interface', methods=['POST'])
def configure_interface_endpoint():
    """Configure a network interface."""
    data = request.json
    success = configure_interface(
        data['name'],
        data['ip_address'],
        data['netmask']
    )
    return jsonify({'success': success})


@app.route('/status', methods=['GET'])
def get_status():
    """Return the current node status."""
    return jsonify(node_config)


@app.route('/config/frr', methods=['POST'])
def configure_frr_endpoint():
    """Update FRR configuration."""
    data = request.json
    success = update_frr_config(data['config_files'])
    return jsonify({'success': success})


@app.route('/config/link', methods=['POST'])
def configure_link_endpoint():
    """Configure a link to another node."""
    data = request.json
    neighbor = data['neighbor']
    node_config['links'][neighbor] = {
        'local_ip': data['local_ip'],
        'remote_ip': data['remote_ip'],
        'interface': data['interface'],
        'status': 'up',
        'delay': data.get('delay', 0)
    }
    
    # Apply TC rules to simulate delay if specified
    if data.get('delay', 0) > 0:
        try:
            # Remove any existing delay rule
            subprocess.run(['tc', 'qdisc', 'del', 'dev', data['interface'], 'root'], 
                          stderr=subprocess.PIPE)
        except Exception:
            pass  # Ignore if no existing rule
        
        # Add new delay rule
        subprocess.run([
            'tc', 'qdisc', 'add', 'dev', data['interface'], 'root', 'netem',
            'delay', f"{data['delay']}ms"
        ])
    
    return jsonify({'success': True})


@app.route('/config/uplink', methods=['POST'])
def configure_uplink_endpoint():
    """Configure an uplink (for ground stations/vessels)."""
    if NODE_TYPE not in ['ground_station', 'vessel']:
        return jsonify({'success': False, 'error': 'Not a ground station or vessel'})
    
    data = request.json
    satellite = data['satellite']
    
    # Add to uplinks list
    uplink = {
        'satellite': satellite,
        'local_ip': data['local_ip'],
        'remote_ip': data['remote_ip'],
        'interface': data['interface'],
        'distance': data.get('distance', 0),
        'delay': data.get('delay', 0),
        'default': data.get('default', False)
    }
    
    # Remove any existing uplink to this satellite
    node_config['uplinks'] = [u for u in node_config['uplinks'] 
                             if u['satellite'] != satellite]
    
    # Add the new uplink
    node_config['uplinks'].append(uplink)

    enforce_network_isolation()
    
    # Set as default route if specified
    if uplink['default']:
        try:
            # Remove existing default route
            subprocess.run(['ip', 'route', 'del', 'default'], stderr=subprocess.PIPE)
            # Add new default route
            subprocess.run(['ip', 'route', 'add', 'default', 'via', uplink['remote_ip']])
        except Exception as e:
            logger.error(f"Failed to set default route: {e}")
    
    # Apply delay if specified
    if uplink.get('delay', 0) > 0:
        try:
            # Remove any existing delay rule
            subprocess.run(['tc', 'qdisc', 'del', 'dev', uplink['interface'], 'root'],
                          stderr=subprocess.PIPE)
        except Exception:
            pass  # Ignore if no existing rule
        
        # Add new delay rule
        subprocess.run([
            'tc', 'qdisc', 'add', 'dev', uplink['interface'], 'root', 'netem',
            'delay', f"{uplink['delay']}ms"
        ])
    
    return jsonify({'success': True})


@app.route('/config/position', methods=['POST'])
def update_position_endpoint():
    """Update the node's position."""
    data = request.json
    node_config['position'] = {
        'lat': data['lat'],
        'lon': data['lon']
    }
    
    # Add altitude for satellites
    if NODE_TYPE == 'satellite' and 'alt' in data:
        node_config['position']['alt'] = data['alt']
        
    return jsonify({'success': True})


@app.route('/execute', methods=['POST'])
def execute_command():
    """Execute a network diagnostic command and return results."""
    allowed_commands = {
        'traceroute': ['/usr/bin/traceroute'],
        'ping': ['/bin/ping', '-c', '4'],
        'ip': ['/sbin/ip', 'route'],
    }
    
    data = request.json
    command = data.get('command', '')
    
    if not command:
        return jsonify({'error': 'No command specified'}), 400
    
    # Extract the base command and parameters
    parts = command.split()
    base_cmd = parts[0]
    
    if base_cmd not in allowed_commands:
        return jsonify({'error': f'Command {base_cmd} not allowed'}), 403
    
    # Build the command with allowed prefix and user parameters
    cmd = allowed_commands[base_cmd] + parts[1:]
    
    try:
        # Execute the command
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30
        )
        
        return jsonify({
            'success': result.returncode == 0,
            'output': result.stdout,
            'error': result.stderr,
            'return_code': result.returncode
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/shutdown', methods=['POST'])
def shutdown_endpoint():
    """Shutdown the node agent."""
    node_config['running'] = False
    return jsonify({'success': True})


if __name__ == '__main__':
    # Start Prometheus metrics server
    start_http_server(8081)
    
    # Start link monitoring in background thread
    monitor_thread = threading.Thread(target=monitor_links, daemon=True)
    monitor_thread.start()
    
    # Register with the controller
    retries = 5
    while retries > 0:
        try:
            requests.post(
                f"{CONTROLLER_URL}/api/node/register",
                json={
                    'name': NODE_NAME,
                    'type': NODE_TYPE,
                    'host': socket.gethostname()
                }
            )
            break
        except requests.exceptions.ConnectionError:
            logger.warning(f"Controller not available, retrying... ({retries} attempts left)")
            retries -= 1
            time.sleep(5)
    
    # Start the Flask app
    app.run(host='0.0.0.0', port=5000)