import React from 'react';

const GroundStationIcon = ({ size = 12, color = 'currentColor' }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill={color}
    stroke={color}
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M4 10a7.31 7.31 0 0 0 10 10Z" />
    <path d="m9 15 3-3" />
    <path d="M17 13a6 6 0 0 0-6-6" />
    <path d="M21 13A10 10 0 0 0 11 3" />
  </svg>
);

export default GroundStationIcon;
