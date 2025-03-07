import React from 'react';

const ShipIcon = ({ size = 12, color = 'currentColor' }) => (
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
    <path d="M2 21c.6.5 1.2 1 2.4 1 2.4 0 2.4-1 4.8-1 2.4 0 2.4 1 4.8 1 2.4 0 2.4-1 4.8-1 1.2 0 1.8.5 2.4 1" />
    <path d="M4 19l-2 2" />
    <path d="M22 19l-2 2" />
    <path d="M12 6H8v5c0 1 .5 2 2 2s2-1 2-2z" />
    <path d="M16 8h0M4 15l16 .01M20 16.01L12 16" />
    <path d="M12 10v6" />
    <path d="M12 3v3" />
  </svg>
);

export default ShipIcon;
