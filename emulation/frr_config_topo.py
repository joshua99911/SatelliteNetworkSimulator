'''
Configure a network topology to function as a set of FRR routers.

Generate IP addresses, interface names, and FRR configurations for a topology
and mark up the topology graph with the information.

Currently assumes all nodes run OSPF in one area.
'''

import ipaddress
import networkx

from emulation import torus_topo


def annotate_graph(graph: networkx.Graph):
    '''
    Annotate a topology with IP address for each node and IP address and interface names
    for each edge.
    '''
    count = 1
    for node in graph.nodes.values():
        # Configure node with an ip address
        node["inf_count"] = 0
        node["number"] = count
        ip = 0x0A010000 + count
        count += 2
        node["ip"] = ipaddress.IPv4Interface((ip, 31))

    count = 1
    for edge in graph.edges.values():
        # Configure edge with a subnet
        edge["number"] = count
        ip = 0x0A0F0000 + count * 4
        count += 1
        edge["ip"] = ipaddress.IPv4Network((ip, 30))

    for n1, n2 in graph.edges:
        # Set ip addresses for each end of an edge
        edge = graph.edges[n1, n2]
        ips = list(edge["ip"].hosts())
        graph.adj[n1][n2]["ip"] = {}
        graph.adj[n1][n2]["ip"][n1] = ipaddress.IPv4Interface((ips[0].packed, 30))
        graph.adj[n2][n1]["ip"][n2] = ipaddress.IPv4Interface((ips[1].packed, 30))

        # Set interface names for each end of an edge
        c = graph.nodes[n1]["inf_count"] + 1
        graph.nodes[n1]["inf_count"] = c
        intf1 = f"{n1}-eth{c}"

        c = graph.nodes[n2]["inf_count"] + 1
        graph.nodes[n2]["inf_count"] = c
        intf2 = f"{n2}-eth{c}"

        graph.adj[n1][n2]["intf"] = {}
        graph.adj[n1][n2]["intf"][n1] = intf1
        graph.adj[n2][n1]["intf"][n2] = intf2

    # Generate config information for the satellites
    for name in torus_topo.satellites(graph):
        node = graph.nodes[name]
        node["ospf"] = create_ospf_config(graph, name)
        node["vtysh"] = create_vtysh_config(name)
        node["daemons"] = create_daemons_config()

    #Generate ip link pool information for both ground stations and vessels
    for node_type in [torus_topo.ground_stations, torus_topo.vessels]:
        for name in node_type(graph):
            node = graph.nodes[name]
            print(node) #CHANGE REMOVE DEBUG
            uplinks = []
            for i in range(4):
                ip = 0x0A0F0000 + count * 4
                count += 1
                nw_link = ipaddress.IPv4Network((ip, 30))
                ips = list(nw_link.hosts())
                uplink = {"nw": nw_link,
                          "ip1": ipaddress.IPv4Interface((ips[0].packed, 30)),
                          "ip2": ipaddress.IPv4Interface((ips[1].packed, 30))}
                uplinks.append(uplink)
            node["uplinks"] = uplinks



OSPF_TEMPLATE = """
hostname {name}
frr defaults datacenter
log syslog informational
ip forwarding
no ipv6 forwarding
service integrated-vtysh-config
!
router ospf
 ospf router-id {ip}
 redistribute static
{networks}
{route_filtering}
exit
!
"""

OSPF_NW_TEMPLATE = """ network {network} area {area}"""

def get_ospf_area(name):
    if name.startswith('G_') or name.startswith('V_'):
        # Each ground station and vessel gets its own area
        # This prevents direct routes between ground stations
        node_id = hash(name) % 254 + 1  # Avoid area 0
        return f"0.0.0.{node_id}"
    else:
        # All satellites in backbone area
        return "0.0.0.0"

# Update the create_ospf_config function
def create_ospf_config(graph: networkx.Graph, name: str) -> str:
    node = graph.nodes[name]
    ip = node.get("ip")  # May be None
    networks = []
    networks_str = []
    route_filtering = ""

    if ip is not None:
        # Make loopback a /32
        network = ipaddress.IPv4Network((ip.ip, 32))
        area = get_ospf_area(name)
        networks_str.append(OSPF_NW_TEMPLATE.format(network=format(network), area=area))

    for neighbor in graph.adj[name]:
        edge = graph.adj[name][neighbor]
        networks.append(edge["ip"][name])
        # Get one of the interface IPs for the router id
        if ip is None:
            ip = edge["ip"][name]

    for network in networks:
        area = get_ospf_area(name)
        networks_str.append(OSPF_NW_TEMPLATE.format(network=format(network), area=area))

    # Add route filtering for ground stations and vessels
    if name.startswith('G_') or name.startswith('V_'):
        # Block direct communication with other ground stations/vessels
        route_filtering = " area 0.0.0.0 virtual-link 0.0.0.1\n"
        route_filtering += " distribute-list SATELLITE_ONLY out\n"
        route_filtering += "!\n"
        route_filtering += "ip prefix-list SATELLITE_ONLY permit 10.1.0.0/16 le 32\n"
    
    # Router ID must be a plain IP, no subnet.
    return OSPF_TEMPLATE.format(
        name=name, 
        ip=format(ip.ip), 
        networks="\n".join(networks_str),
        route_filtering=route_filtering
    )


def create_daemons_config() -> str:
    return """#
ospfd=yes
vtysh_enable=yes
zebra_options="  -A 127.0.0.1 -s 90000000"
mgmtd_options="  -A 127.0.0.1"
ospfd_options="  -A 127.0.0.1"
    """


def create_vtysh_config(name: str) -> str:
    return """service integrated-vtysh-config
hostname {name}""".format(
        name=name
    )


def dump_graph(graph: networkx.Graph):
    for name, node in graph.nodes.items():
        ip = node.get("ip")
        if ip is not None:
            ip = format(ip)
        else:
            ip = ""

        print(f"node: {name} - {ip}")
        for neighbor in graph.adj[name]:
            edge = graph.adj[name][neighbor]
            print(
                f'\t{format(edge["ip"][name])}  : {edge["intf"][name]} to {neighbor} ({format(edge["ip"][neighbor])})'
            )

    print()
    for n, edge in graph.edges.items():
        print(f'edge: {n} - {format(edge["ip"])}')


def gen_test_graph() -> networkx.Graph:
    graph = networkx.Graph()

    graph.add_node("R1")
    graph.nodes["R1"][torus_topo.TYPE] = torus_topo.TYPE_SAT
    graph.add_node("R2")
    graph.nodes["R2"][torus_topo.TYPE] = torus_topo.TYPE_SAT
    graph.add_node("R3")
    graph.nodes["R3"][torus_topo.TYPE] = torus_topo.TYPE_SAT
    graph.add_node("R4")
    graph.nodes["R4"][torus_topo.TYPE] = torus_topo.TYPE_SAT

    graph.add_edge("R1", "R2")
    graph.add_edge("R2", "R3")
    graph.add_edge("R3", "R4")
    graph.add_edge("R4", "R1")
    return graph

def test_config_graph() -> bool:
    g = gen_test_graph()
    annotate_graph(g)
    dump_graph(g)
    return True


if __name__ == "__main__":
    # Run a simple test
    test_config_graph()
