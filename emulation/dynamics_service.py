'''
Dynamics Service - Simulates satellite movement, calculates visibility and link conditions
Based on the original geosimsat.py
'''

import os
import sys
import time
import json
import configparser
import datetime
import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any, Set, Union

import requests
import networkx as nx
from skyfield.api import load, wgs84
from skyfield.api import EarthSatellite
from skyfield.positionlib import Geocentric
from skyfield.toposlib import GeographicPosition
from skyfield.units import Angle, Distance

from emulation import simapi
from emulation import torus_topo

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/app/logs/dynamics.log')
    ]
)
logger = logging.getLogger('dynamics')

# Configuration
CONFIG_FILE = os.environ.get('CONFIG_FILE', '/app/emulation/mnet/configs/small.net')
CONTROLLER_URL = os.environ.get('CONTROLLER_URL', 'http://controller:8000')


@dataclass
class Satellite:
    '''Represents an instance of a satellite'''
    name: str
    earth_sat: EarthSatellite
    geo: Geocentric = None
    lat: Angle = 0
    lon: Angle = 0
    height: Distance = 0
    inter_plane_status: bool = True
    prev_inter_plane_status: bool = True


@dataclass
class Uplink:
    '''Represents a link between the ground and a satellite'''
    satellite_name: str
    ground_name: str
    distance: int
    delay: float = 1.0  # Default delay in milliseconds


@dataclass
class GroundStation:
    '''Represents an instance of a ground station'''
    name: str
    position: GeographicPosition
    uplinks: list[Uplink] = field(default_factory=list)


@dataclass
class Waypoint:
    """Represents a waypoint for a vessel's journey"""
    lat: float
    lon: float


@dataclass
class MovingStation(GroundStation):
    '''Represents an instance of a moving station (vessel)'''
    waypoints: list[Waypoint] = field(default_factory=list)
    current_waypoint_index: int = 0
    next_waypoint_index: int = 1
    moving_forward: bool = True
    SPEED: float = 1.0  # degrees per update

    def update_position(self) -> None:
        """Update the vessel's position based on constant speed movement"""
        if not self.waypoints or len(self.waypoints) < 2:
            return

        current_lat = float(self.position.latitude.degrees)
        current_lon = float(self.position.longitude.degrees)
        current_wp = self.waypoints[self.current_waypoint_index]
        next_wp = self.waypoints[self.next_waypoint_index]

        # Calculate direction vector
        delta_lat = next_wp.lat - current_wp.lat
        delta_lon = next_wp.lon - current_wp.lon
        
        # Normalize direction vector
        distance = (delta_lat ** 2 + delta_lon ** 2) ** 0.5
        if distance > 0:
            move_lat = (delta_lat / distance) * self.SPEED
            move_lon = (delta_lon / distance) * self.SPEED
        else:
            move_lat = move_lon = 0

        # Update position
        new_lat = current_lat + move_lat
        new_lon = current_lon + move_lon

        # Check if we've reached the next waypoint
        new_distance = ((new_lat - next_wp.lat) ** 2 + (new_lon - next_wp.lon) ** 2) ** 0.5
        if new_distance < self.SPEED:
            # We've reached the waypoint, update indices
            if self.moving_forward:
                if self.next_waypoint_index == len(self.waypoints) - 1:
                    # Reached last waypoint, reverse direction
                    self.moving_forward = False
                    self.current_waypoint_index = self.next_waypoint_index
                    self.next_waypoint_index = self.current_waypoint_index - 1
                else:
                    # Move to next waypoint
                    self.current_waypoint_index = self.next_waypoint_index
                    self.next_waypoint_index += 1
            else:
                if self.next_waypoint_index == 0:
                    # Reached first waypoint, reverse direction
                    self.moving_forward = True
                    self.current_waypoint_index = 0
                    self.next_waypoint_index = 1
                else:
                    # Move to previous waypoint
                    self.current_waypoint_index = self.next_waypoint_index
                    self.next_waypoint_index -= 1

        # Update the position using wgs84.latlon
        self.position = wgs84.latlon(new_lat, new_lon)


class SatelliteDynamics:
    '''Runs simulations to update satellite positions and link states'''
    
    # Simulation parameters
    TIME_SLICE = 10  # seconds
    MIN_ELEVATION = 15  # degrees

    def __init__(self, graph: nx.Graph):
        self.graph = graph
        self.ts = load.timescale()
        self.satellites: list[Satellite] = []
        self.ground_stations: list[GroundStation] = []
        self.moving_stations: list[MovingStation] = []
        self.min_elevation = SatelliteDynamics.MIN_ELEVATION
        self.zero_uplink_count = 0
        self.uplink_updates = 0
        
        # Initialize satellites
        for name in torus_topo.satellites(graph):
            orbit = graph.nodes[name]["orbit"]
            ts = load.timescale()
            l1, l2 = orbit.tle_format()
            earth_satellite = EarthSatellite(l1, l2, name, ts)
            satellite = Satellite(name, earth_satellite)
            self.satellites.append(satellite)
        
        # Initialize ground stations
        for name in torus_topo.ground_stations(graph):
            node = graph.nodes[name]
            position = wgs84.latlon(node[torus_topo.LAT], node[torus_topo.LON])
            ground_station = GroundStation(name, position)
            self.ground_stations.append(ground_station)
            
        # Initialize vessels
        for name in torus_topo.vessels(graph):
            node = graph.nodes[name]
            position = wgs84.latlon(node[torus_topo.LAT], node[torus_topo.LON])
            # Convert tuple waypoints to Waypoint objects
            waypoints = [Waypoint(lat=wp[0], lon=wp[1]) for wp in node["waypoints"]]
            moving_station = MovingStation(
                name=name,
                position=position,
                waypoints=waypoints
            )
            self.moving_stations.append(moving_station)

    def calculate_positions(self, future_time: datetime.datetime):
        """Calculate positions of satellites, ground stations, and vessels"""
        sfield_time = self.ts.from_datetime(future_time)
        satellites_positions = []
        ground_positions = []
        vessel_positions = []
        
        # Calculate satellite positions
        for satellite in self.satellites:
            satellite.geo = satellite.earth_sat.at(sfield_time)
            lat, lon = wgs84.latlon_of(satellite.geo)
            satellite.lat = lat
            satellite.lon = lon
            satellite.height = wgs84.height_of(satellite.geo)
            
            # Add to positions list
            satellite_pos = simapi.SatellitePosition(
                name=satellite.name,
                lat=float(satellite.lat.degrees),
                lon=float(satellite.lon.degrees),
                height=float(satellite.height.km)
            )
            satellites_positions.append(satellite_pos)
        
        # Add ground station positions
        for station in self.ground_stations:
            ground_pos = simapi.GroundStationPosition(
                name=station.name,
                lat=float(station.position.latitude.degrees),
                lon=float(station.position.longitude.degrees)
            )
            ground_positions.append(ground_pos)
            
        # Update vessel positions
        for vessel in self.moving_stations:
            vessel.update_position()
            vessel_pos = simapi.VesselPosition(
                name=vessel.name,
                lat=float(vessel.position.latitude.degrees),
                lon=float(vessel.position.longitude.degrees)
            )
            vessel_positions.append(vessel_pos)
            
        return satellites_positions, ground_positions, vessel_positions

    @staticmethod
    def nearby(ground_station: GroundStation, satellite: Satellite) -> bool:
        """Check if a satellite is possibly visible from a ground station"""
        return (satellite.lon.degrees > ground_station.position.longitude.degrees - 20 and
                satellite.lon.degrees < ground_station.position.longitude.degrees + 20 and
                satellite.lat.degrees > ground_station.position.latitude.degrees - 20 and 
                satellite.lat.degrees < ground_station.position.latitude.degrees + 20)

    def calculate_uplinks(self, future_time: datetime.datetime):
        """Calculate uplinks between ground stations/vessels and satellites"""
        self.uplink_updates += 1
        zero_uplinks = False
        ground_uplinks = []
        
        sfield_time = self.ts.from_datetime(future_time)
        
        # Process both ground stations and vessels
        all_stations = self.ground_stations + self.moving_stations
        
        for station in all_stations:
            # Keep track of existing uplinks but update their parameters
            current_uplinks = {uplink.satellite_name: uplink for uplink in station.uplinks}
            station.uplinks = []
            
            # List to store uplinks for this station
            uplinks_list = []
            
            for satellite in self.satellites:
                # Only calculate for satellites in the vicinity
                if SatelliteDynamics.nearby(station, satellite):
                    difference = satellite.earth_sat - station.position
                    topocentric = difference.at(sfield_time)
                    alt, az, d = topocentric.altaz()
                    
                    # If the satellite is above the minimum elevation angle
                    if alt.degrees > self.min_elevation:
                        delay = calculate_link_delay(d.km)
                        
                        # Create or update uplink
                        if satellite.name in current_uplinks:
                            # Update existing uplink
                            uplink = current_uplinks[satellite.name]
                            uplink.distance = d.km
                            uplink.delay = delay
                        else:
                            # Create new uplink
                            uplink = Uplink(satellite.name, station.name, d.km, delay=delay)
                        
                        station.uplinks.append(uplink)
                        
                        # Add to uplinks for the API
                        uplinks_list.append(simapi.UpLink(
                            sat_node=satellite.name,
                            distance=int(d.km),
                            delay=delay
                        ))
                        
                        logger.debug(f"Station {station.name}, Satellite {satellite.name}: "
                                    f"alt={alt.degrees}, az={az.degrees}, distance={d.km}km, delay={delay}ms")
            
            # Add uplinks for this station to the list
            if uplinks_list:
                ground_uplinks.append(simapi.UpLinks(
                    ground_node=station.name,
                    uplinks=uplinks_list
                ))
            elif len(station.uplinks) == 0:
                zero_uplinks = True
        
        if zero_uplinks:
            self.zero_uplink_count += 1
        
        return ground_uplinks

    def calculate_satellite_links(self):
        """Calculate which satellite-to-satellite links are available"""
        satellite_links = []
        inclination = self.graph.graph["inclination"]
        
        # Update inter-plane status for each satellite
        for satellite in self.satellites:
            # Track if state changed
            satellite.prev_inter_plane_status = satellite.inter_plane_status
            
            # Satellites above a certain latitude can't maintain cross-plane links
            if satellite.lat.degrees > (inclination - 2) or satellite.lat.degrees < (-inclination + 2):
                satellite.inter_plane_status = False
            else:
                satellite.inter_plane_status = True
        
        # Create link objects for all satellite links
        for node1, node2 in self.graph.edges():
            if node1.startswith('R') and node2.startswith('R'):  # Satellite nodes start with R
                edge = self.graph.edges[node1, node2]
                link_status = True
                
                # Check if this is an inter-ring link
                if edge.get("inter_ring", False):
                    # Find the satellites
                    sat1 = next((s for s in self.satellites if s.name == node1), None)
                    sat2 = next((s for s in self.satellites if s.name == node2), None)
                    
                    if sat1 and sat2:
                        # Inter-ring links go down when either satellite is at high latitude
                        link_status = sat1.inter_plane_status and sat2.inter_plane_status
                
                # Calculate delay based on distance
                sat1_pos = next((s for s in self.satellites if s.name == node1), None)
                sat2_pos = next((s for s in self.satellites if s.name == node2), None)
                
                if sat1_pos and sat2_pos:
                    # Calculate distance between satellites (simplified)
                    lat1, lon1 = sat1_pos.lat.degrees, sat1_pos.lon.degrees
                    lat2, lon2 = sat2_pos.lat.degrees, sat2_pos.lon.degrees
                    alt1, alt2 = sat1_pos.height.km, sat2_pos.height.km
                    
                    # Very simplified distance calculation - in a real system you'd use proper 3D coordinates
                    earth_radius = 6378.0  # km
                    # Convert to cartesian coordinates
                    import math
                    phi1, phi2 = math.radians(90 - lat1), math.radians(90 - lat2)
                    theta1, theta2 = math.radians(lon1), math.radians(lon2)
                    
                    r1 = earth_radius + alt1
                    r2 = earth_radius + alt2
                    
                    x1 = r1 * math.sin(phi1) * math.cos(theta1)
                    y1 = r1 * math.sin(phi1) * math.sin(theta1)
                    z1 = r1 * math.cos(phi1)
                    
                    x2 = r2 * math.sin(phi2) * math.cos(theta2)
                    y2 = r2 * math.sin(phi2) * math.sin(theta2)
                    z2 = r2 * math.cos(phi2)
                    
                    distance = math.sqrt((x2-x1)**2 + (y2-y1)**2 + (z2-z1)**2)
                    delay = calculate_link_delay(distance)
                else:
                    delay = 1.0  # Default delay
                
                satellite_links.append(simapi.Link(
                    node1_name=node1,
                    node2_name=node2,
                    up=link_status,
                    delay=delay
                ))
        
        return satellite_links

    def run_simulation(self):
        """Run the full simulation"""
        running = True
        current_time = datetime.datetime.now(tz=datetime.timezone.utc)
        slice_delta = datetime.timedelta(seconds=SatelliteDynamics.TIME_SLICE)
        
        while running:
            try:
                # Calculate for next time step
                future_time = current_time + slice_delta
                logger.info(f"Simulating positions for {future_time}")
                
                # Calculate positions
                satellite_positions, ground_positions, vessel_positions = self.calculate_positions(future_time)
                
                # Calculate uplinks
                ground_uplinks = self.calculate_uplinks(future_time)
                
                # Calculate satellite links
                satellite_links = self.calculate_satellite_links()
                
                # Create data object
                data = simapi.GraphData(
                    satellites=satellite_positions,
                    ground_stations=ground_positions,
                    vessels=vessel_positions,
                    satellite_links=satellite_links,
                    ground_uplinks=ground_uplinks
                )
                
                # Send updates to controller
                try:
                    response = requests.put(f"{CONTROLLER_URL}/positions", json=data.model_dump())
                    logger.info(f"Sent position updates to controller: {response.status_code}")
                except Exception as e:
                    logger.error(f"Failed to send updates to controller: {e}")
                
                # Wait until the simulation time
                sleep_delta = future_time - datetime.datetime.now(tz=datetime.timezone.utc)
                sleep_seconds = sleep_delta.total_seconds()
                
                if sleep_seconds > 0:
                    logger.info(f"Waiting {sleep_seconds} seconds until next update")
                    time.sleep(sleep_seconds)
                
                # Update current time
                current_time = future_time
                
            except KeyboardInterrupt:
                logger.info("Simulation stopped by user")
                running = False
            except Exception as e:
                logger.error(f"Error in simulation: {e}", exc_info=True)
                time.sleep(5)  # Sleep briefly to avoid rapid failures


def calculate_link_delay(distance_km: float) -> float:
    '''
    Calculate link delay based on distance.
    
    Args:
        distance_km: Distance in kilometers between nodes
        
    Returns:
        Delay in milliseconds
        
    The delay consists of:
    - Propagation delay (speed of light through vacuum)
    - Processing/equipment delay (fixed component)
    '''
    SPEED_OF_LIGHT = 299792.458  # km/s
    PROCESSING_DELAY = 1  # ms (fixed component for equipment/processing)
    
    # Calculate propagation delay (distance/speed)
    prop_delay = (distance_km / SPEED_OF_LIGHT) * 1000  # Convert to ms
    
    # Add fixed processing delay
    total_delay = prop_delay + PROCESSING_DELAY
    
    return round(total_delay, 3)  # Round to 3 decimal places


def load_network_config(config_file: str) -> nx.Graph:
    """Load network configuration from file"""
    # Parse the configuration file
    parser = configparser.ConfigParser()
    parser.optionxform = str  # Keep case sensitivity
    
    try:
        parser.read(config_file)
        
        # Extract network parameters
        num_rings = parser['network'].getint('rings', 4)
        num_routers = parser['network'].getint('routers', 4)
        use_ground_stations = parser['network'].getboolean('ground_stations', False)
        
        # Extract physical parameters
        inclination = parser.getfloat('constellation', 'inclination', fallback=53.9)
        altitude = parser.getfloat('constellation', 'altitude', fallback=550)
        
        # Create ground station data
        ground_station_data = {}
        if use_ground_stations and 'ground_stations' in parser:
            for name, coords in parser['ground_stations'].items():
                lat, lon = map(float, coords.split(','))
                ground_station_data[name] = (lat, lon)
        
        # Create vessel data
        vessel_data = {}
        if 'vessels' in parser:
            for name, waypoint_str in parser['vessels'].items():
                waypoints = []
                for waypoint in waypoint_str.split(';'):
                    lat, lon = map(float, waypoint.split(','))
                    waypoints.append((lat, lon))
                vessel_data[name] = waypoints
        
        # Create the network
        graph = torus_topo.create_network(
            num_rings=num_rings,
            num_ring_nodes=num_routers,
            ground_stations=use_ground_stations,
            ground_station_data=ground_station_data,
            vessel_data=vessel_data,
            inclination=inclination,
            altitude=altitude
        )
        
        # Annotate the graph with FRR config data
        from emulation import frr_config_topo
        frr_config_topo.annotate_graph(graph)
        
        logger.info(f"Loaded network configuration: {num_rings} rings, {num_routers} routers per ring")
        return graph
        
    except Exception as e:
        logger.error(f"Error loading network configuration: {e}", exc_info=True)
        return None


def run(config_file: str):
    """Run the simulation with the given configuration"""
    # Load network configuration
    graph = load_network_config(config_file)
    if not graph:
        logger.error("Failed to load network configuration")
        return
    
    # Create dynamics simulator
    simulator = SatelliteDynamics(graph)
    
    # Run the simulation
    simulator.run_simulation()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        config_file = sys.argv[1]
    else:
        config_file = CONFIG_FILE
    
    logger.info(f"Starting satellite dynamics simulation with config: {config_file}")
    run(config_file)