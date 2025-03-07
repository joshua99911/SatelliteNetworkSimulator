import React, { useState, useEffect } from 'react';
import SatelliteIcon from './icons/SatelliteIcon';
import GroundStationIcon from './icons/GroundStationIcon';
import ShipIcon from './icons/ShipIcon';
import { clamp, getNodeCoordinates, areInSamePlane } from '../utils/helpers';

function SatelliteMap() {
  const mapWidth = 800;
  const mapHeight = 400;
  const originalImageWidth = 5400;
  const originalImageHeight = 2700;
  const MAX_SCALE = Math.max(originalImageWidth / mapWidth, originalImageHeight / mapHeight);
  const MIN_SCALE = 1;

  const [satellites, setSatellites] = useState([]);
  const [groundStations, setGroundStations] = useState([]);
  const [vessels, setVessels] = useState([]);
  const [satelliteLinks, setSatelliteLinks] = useState([]);
  const [groundUplinks, setGroundUplinks] = useState([]);
  const [showInPlaneLinks, setShowInPlaneLinks] = useState(false);
  const [showCrossPlaneLinks, setShowCrossPlaneLinks] = useState(false);
  const [showGroundLinks, setShowGroundLinks] = useState(false);
  const [hoveredNode, setHoveredNode] = useState(null);
  const [transform, setTransform] = useState({ x: 0, y: 0, scale: 1 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });

  useEffect(() => {
    const fetchPositions = async () => {
      try {
        const res = await fetch('/positions');
        const data = await res.json();
        console.log("Received data:", data);
        setSatellites(data.satellites || []);
        setGroundStations(data.ground_stations || []);
        setVessels(data.vessels || []);
        setSatelliteLinks(data.satellite_links || []);
        setGroundUplinks(data.ground_uplinks || []);
      } catch (err) {
        console.error('Error fetching positions:', err);
      }
    };

    fetchPositions();
    const interval = setInterval(fetchPositions, 10000);
    return () => clearInterval(interval);
  }, []);

  function handleMouseDown(e) {
    if (e.button === 0) {
      setIsDragging(true);
      setDragStart({ x: e.clientX - transform.x, y: e.clientY - transform.y });
    }
  }

  function handleMouseMove(e) {
    if (isDragging) {
      const newX = e.clientX - dragStart.x;
      const newY = e.clientY - dragStart.y;
      const maxX = mapWidth * (transform.scale - 1);
      const maxY = mapHeight * (transform.scale - 1);
      setTransform(prev => ({
        ...prev,
        x: clamp(newX, -maxX, 0),
        y: clamp(newY, -maxY, 0)
      }));
    }
  }

  function handleMouseUp() {
    setIsDragging(false);
  }

  function handleMapEnter() {
    document.body.style.overflow = 'hidden';
  }

  function handleMapLeave() {
    document.body.style.overflow = 'auto';
    setIsDragging(false);
  }

  function findNode(name) {
    return satellites.find(s => s.name === name) ||
           groundStations.find(g => g.name === name) ||
           vessels.find(v => v.name === name);
  }

  function getConnectedNodes(nodeName) {
    const connections = new Set();
    satelliteLinks.forEach(link => {
      if (link.up) {
        if (link.node1_name === nodeName) {
          connections.add(link.node2_name);
        } else if (link.node2_name === nodeName) {
          connections.add(link.node1_name);
        }
      }
    });
    groundUplinks.forEach(station => {
      if (station.ground_node === nodeName) {
        station.uplinks.forEach(uplink => connections.add(uplink.sat_node));
      } else {
        station.uplinks.forEach(uplink => {
          if (uplink.sat_node === nodeName) {
            connections.add(station.ground_node);
          }
        });
      }
    });
    return connections;
  }

  function handleNodeClick(nodeName) {
    const path = nodeName.startsWith('R') ? 'router' :
                 nodeName.startsWith('G_') ? 'station' : 'vessel';
    window.open(`/view/${path}/${nodeName}`, '_blank');
  }

  function renderLinks() {
    const elements = [];
    let keyCounter = 0;

    function drawLink(node1Name, node2Name, color, opacity, keyPrefix) {
      const node1 = findNode(node1Name);
      const node2 = findNode(node2Name);
      if (!node1 || !node2) return;
      const [x1, y1] = getNodeCoordinates(node1, mapWidth, mapHeight);
      const [x2, y2] = getNodeCoordinates(node2, mapWidth, mapHeight);
      const dx = x2 - x1;
      const strokeWidth = 2 / transform.scale;

      if (Math.abs(dx) > mapWidth / 2) {
        if (x1 < x2) {
          elements.push(
            <line key={`${keyPrefix}-left`} x1={x1} y1={y1} x2={x2 - mapWidth} y2={y2} stroke={color} strokeWidth={strokeWidth} strokeOpacity={opacity} />,
            <line key={`${keyPrefix}-right`} x1={x1 + mapWidth} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth={strokeWidth} strokeOpacity={opacity} />
          );
        } else {
          elements.push(
            <line key={`${keyPrefix}-left`} x1={x1 - mapWidth} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth={strokeWidth} strokeOpacity={opacity} />,
            <line key={`${keyPrefix}-right`} x1={x1} y1={y1} x2={x2 + mapWidth} y2={y2} stroke={color} strokeWidth={strokeWidth} strokeOpacity={opacity} />
          );
        }
      } else {
        elements.push(
          <line key={keyPrefix} x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth={strokeWidth} strokeOpacity={opacity} />
        );
      }
    }

    if (hoveredNode) {
      const connected = getConnectedNodes(hoveredNode);
      connected.forEach(otherName => {
        const isGroundLink = hoveredNode.startsWith('G_') || hoveredNode.startsWith('V_') ||
                             otherName.startsWith('G_') || otherName.startsWith('V_');
        const color = isGroundLink ? '#ef4444' : (areInSamePlane(hoveredNode, otherName) ? '#22c55e' : '#1d4ed8');
        drawLink(hoveredNode, otherName, color, 1, `hover-${keyCounter++}`);
      });
      return elements;
    }

    if (showInPlaneLinks || showCrossPlaneLinks) {
      satelliteLinks.forEach((link, idx) => {
        if (link.up) {
          const inPlane = areInSamePlane(link.node1_name, link.node2_name);
          if ((inPlane && showInPlaneLinks) || (!inPlane && showCrossPlaneLinks)) {
            const color = inPlane ? '#22c55e' : '#1d4ed8';
            drawLink(link.node1_name, link.node2_name, color, 0.5, `sat-${idx}`);
          }
        }
      });
    }

    if (showGroundLinks) {
      groundUplinks.forEach((station, sIdx) => {
        station.uplinks.forEach((uplink, uIdx) => {
          if (uplink) {
            drawLink(station.ground_node, uplink.sat_node, '#ef4444', 0.5, `gs-${sIdx}-${uIdx}`);
          }
        });
      });
    }
    return elements;
  }

  function getIconComponent(node) {
    if (node.name.startsWith('G_')) {
      return GroundStationIcon;
    } else if (node.name.startsWith('V_')) {
      return ShipIcon;
    }
    return SatelliteIcon;
  }

  function renderNodes() {
    const allNodes = [...satellites, ...groundStations, ...vessels];
    return allNodes.map(node => {
      const [x, y] = getNodeCoordinates(node, mapWidth, mapHeight);
      const isHovered = hoveredNode === node.name;
      const iconSize = 12;
      const defaultColor = node.name.startsWith('G_') ? '#22c55e' :
                           node.name.startsWith('V_') ? '#1d4ed8' : '#1d4ed8';
      const hoverColor = 'red';
      const iconColor = isHovered ? hoverColor : defaultColor;
      const textColor = isHovered ? hoverColor : defaultColor;
      const IconComponent = getIconComponent(node);

      return (
        <g key={node.name}
           transform={`translate(${x - iconSize / 2}, ${y - iconSize / 2})`}
           style={{ cursor: 'pointer' }}
           onMouseEnter={(e) => { e.stopPropagation(); setHoveredNode(node.name); }}
           onMouseLeave={(e) => { e.stopPropagation(); setHoveredNode(null); }}
           onClick={(e) => { e.stopPropagation(); handleNodeClick(node.name); }}>
          <IconComponent size={iconSize} color={iconColor} />
          <text x={iconSize + 4} y={iconSize / 2} fontSize={12} fill={textColor} alignmentBaseline="middle">
            {node.name}
          </text>
        </g>
      );
    });
  }

  function handleWheel(e) {
    e.preventDefault();
    const scaleFactor = e.deltaY > 0 ? 0.9 : 1.1;
    const newScale = clamp(transform.scale * scaleFactor, MIN_SCALE, MAX_SCALE);
    if ((transform.scale === MAX_SCALE && scaleFactor > 1) ||
        (transform.scale === MIN_SCALE && scaleFactor < 1)) {
      return;
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;
    const zoomPointX = (mouseX - transform.x) / transform.scale;
    const zoomPointY = (mouseY - transform.y) / transform.scale;
    const newX = mouseX - zoomPointX * newScale;
    const newY = mouseY - zoomPointY * newScale;
    const maxX = mapWidth * (newScale - 1);
    const maxY = mapHeight * (newScale - 1);
    setTransform({
      scale: newScale,
      x: clamp(newX, -maxX, 0),
      y: clamp(newY, -maxY, 0)
    });
  }

  return (
    <div className="w-full max-w-4xl mt-4">
      <div className="mb-4 flex gap-4">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={showInPlaneLinks} onChange={(e) => setShowInPlaneLinks(e.target.checked)} />
          Show In-Plane Links
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={showCrossPlaneLinks} onChange={(e) => setShowCrossPlaneLinks(e.target.checked)} />
          Show Cross-Plane Links
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={showGroundLinks} onChange={(e) => setShowGroundLinks(e.target.checked)} />
          Show Up Links
        </label>
      </div>
      <div
        className="relative w-full h-96 border border-gray-200 rounded-lg overflow-hidden"
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseEnter={handleMapEnter}
        onMouseLeave={handleMapLeave}
      >
        <svg viewBox={`0 0 ${mapWidth} ${mapHeight}`} className="w-full h-full" style={{ backgroundColor: '#f0f0f0' }}>
          <g transform={`translate(${transform.x}, ${transform.y}) scale(${transform.scale})`}>
            <image href="/static/images/worldmap.jpg" x={0} y={0} width={mapWidth} height={mapHeight} preserveAspectRatio="none" />
            {renderLinks()}
            {renderNodes()}
          </g>
        </svg>
      </div>
    </div>
  );
}

export default SatelliteMap;
