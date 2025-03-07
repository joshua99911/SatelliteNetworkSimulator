'''
Client to drive the JSON api implemented in driver.py
'''
import requests
from emulation import simapi

class Client:
    def __init__(self, url: str) -> None:
        self.url = url

    def set_link_state(self, node1: str, node2: str, up: bool) -> None:
        try:
            print(f"send link state {node1}, {node2}, state up {up}")
            data = simapi.Link(node1_name=node1, node2_name=node2, up="true" if up else "false")
            url = f"{self.url}/link"
            r = requests.put(url, 
                    json=data.model_dump())
            print(r.text)
        except requests.exceptions.ConnectionError as e:
            print(e)
            pass

    def set_uplinks(self, ground_node: str, links: list[tuple[str, int, float]]) -> None:
        '''
        Send uplink updates to the server
        
        Args:
            ground_node: Name of the ground station
            links: List of tuples containing (satellite_name, distance, delay)
        '''
        try:
            print(f"send up links: {ground_node}")
            data = simapi.UpLinks(ground_node=ground_node, uplinks=[])
            for link in links:
                data.uplinks.append(simapi.UpLink(
                    sat_node=link[0], 
                    distance=link[1],
                    delay=link[2]
                ))
            url = f"{self.url}/uplinks"
            print(data.model_dump())
            r = requests.put(url, json=data.model_dump())
            print(r.text)
        except requests.exceptions.ConnectionError as e:
            print(e)

    def update_positions(self, positions: simapi.GraphData) -> None:
        try:
            print("Sending satellite and ground station positions update")
            url = f"{self.url}/positions"
            r = requests.put(url, json=positions.model_dump())
            print(r.text)
        except requests.exceptions.ConnectionError as e:
            print(e)