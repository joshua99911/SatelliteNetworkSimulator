export function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }
  
  export function getNodeCoordinates(node, mapWidth, mapHeight) {
    const x = ((node.lon + 180) / 360) * mapWidth;
    const y = ((90 - node.lat) / 180) * mapHeight;
    return [x, y];
  }
  
  export function areInSamePlane(sat1Name, sat2Name) {
    const ring1 = parseInt(sat1Name.split('_')[0].substring(1));
    const ring2 = parseInt(sat2Name.split('_')[0].substring(1));
    return ring1 === ring2;
  }
  