import React from 'react';
import ReactDOM from 'react-dom';
import SatelliteMap from './components/SatelliteMap';
import DatabaseMonitor from './components/DatabaseMonitor';

ReactDOM.render(<SatelliteMap />, document.getElementById('satellite-map'));
ReactDOM.render(<DatabaseMonitor />, document.getElementById('database-monitor'));
