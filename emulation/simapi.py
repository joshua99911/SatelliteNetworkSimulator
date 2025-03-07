'''
Definitions for elements of the Simulator API

The server side of the API is implemented in mnet/driver.py
The client side is implemented in mnet/client.py
'''

from pydantic import BaseModel

class Link(BaseModel):
    node1_name: str
    node2_name: str
    up: bool
    delay: float = 1.0  # Default 1ms delay

class UpLink(BaseModel):
    sat_node: str
    distance: int
    delay: float = 1.0  # Default 1ms delay

class UpLinks(BaseModel):
    ground_node: str
    uplinks: list[UpLink]

class SatellitePosition(BaseModel):
    name: str
    lat: float
    lon: float
    height: float

class GroundStationPosition(BaseModel):
    name: str
    lat: float
    lon: float

class VesselPosition(BaseModel):
    name: str
    lat: float
    lon: float

class GraphData(BaseModel):
    satellites: list[SatellitePosition]
    ground_stations: list[GroundStationPosition]
    vessels: list[VesselPosition]
    satellite_links: list[Link]
    ground_uplinks: list[UpLinks]