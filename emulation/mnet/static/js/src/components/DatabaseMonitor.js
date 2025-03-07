// /home/ubuntu/satellites3/emulation/mnet/static/js/src/components/DatabaseMonitor.js

import React, { useState, useEffect } from 'react';

const DatabaseMonitor = () => {
  const [databases, setDatabases] = useState(null);
  const [selectedDb, setSelectedDb] = useState(null);
  const [dbData, setDbData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Fetch list of databases
  const fetchDatabases = async () => {
    try {
      const response = await fetch('/monitor/databases');
      const data = await response.json();
      setDatabases(data);
    } catch (err) {
      setError('Failed to fetch database list');
    }
  };

  // Fetch data for selected database
  const fetchDbData = async (dbPath) => {
    if (!dbPath) return;
    
    setLoading(true);
    try {
      const response = await fetch(`/monitor/data/${encodeURIComponent(dbPath)}`);
      const data = await response.json();
      setDbData(data);
      setError(null);
    } catch (err) {
      setError('Failed to fetch database data');
      setDbData(null);
    }
    setLoading(false);
  };

  // Initial load
  useEffect(() => {
    fetchDatabases();
    const interval = setInterval(fetchDatabases, 10000);
    return () => clearInterval(interval);
  }, []);

  // Fetch data when database selection changes
  useEffect(() => {
    if (selectedDb) {
      fetchDbData(selectedDb);
      const interval = setInterval(() => fetchDbData(selectedDb), 5000);
      return () => clearInterval(interval);
    }
  }, [selectedDb]);

  return (
    <div className="w-full max-w-4xl mt-4">
      {/* Database Selection */}
      <div className="mb-4">
        <h3 className="text-lg font-semibold mb-2 flex items-center gap-2">
          Monitor Databases
        </h3>
        
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Master Database */}
          {databases?.master && (
            <button
              onClick={() => setSelectedDb(databases.master)}
              className={`p-4 border rounded-lg text-left hover:bg-gray-50 transition-colors
                ${selectedDb === databases.master ? 'border-blue-500 bg-blue-50' : 'border-gray-200'}`}
            >
              <div className="font-medium">Master Database</div>
              <div className="text-sm text-gray-500 truncate">{databases.master}</div>
            </button>
          )}
          
          {/* Node Databases */}
          {databases?.nodes && Object.entries(databases.nodes).map(([name, path]) => (
            <button
              key={name}
              onClick={() => setSelectedDb(path)}
              className={`p-4 border rounded-lg text-left hover:bg-gray-50 transition-colors
                ${selectedDb === path ? 'border-blue-500 bg-blue-50' : 'border-gray-200'}`}
            >
              <div className="font-medium">{name}</div>
              <div className="text-sm text-gray-500 truncate">{path}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Data Display */}
      {selectedDb && (
        <div className="border rounded-lg p-4">
          <div className="flex justify-between items-center mb-4">
            <h4 className="font-medium">Database Contents</h4>
            <button 
              onClick={() => fetchDbData(selectedDb)}
              className="text-blue-500 hover:text-blue-600 flex items-center gap-2"
            >
              Refresh
            </button>
          </div>

          {loading && <div className="text-gray-500">Loading...</div>}
          
          {error && (
            <div className="text-red-500 flex items-center gap-2">
              {error}
            </div>
          )}
          
          {dbData && (
            <>
              {/* Statistics */}
              <div className="grid grid-cols-3 gap-4 mb-4">
                <div className="border rounded-lg p-4">
                  <div className="text-sm text-gray-500">Total Targets</div>
                  <div className="text-2xl font-semibold">{dbData.stats.total}</div>
                </div>
                <div className="border rounded-lg p-4">
                  <div className="text-sm text-gray-500">Responding</div>
                  <div className="text-2xl font-semibold">{dbData.stats.responding}</div>
                </div>
                <div className="border rounded-lg p-4">
                  <div className="text-sm text-gray-500">Response Rate</div>
                  <div className="text-2xl font-semibold">
                    {dbData.stats.response_rate.toFixed(1)}%
                  </div>
                </div>
              </div>

              {/* Data Table */}
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead>
                    <tr>
                      {dbData.columns.map(column => (
                        <th 
                          key={column}
                          className="px-6 py-3 bg-gray-50 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
                        >
                          {column}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="bg-white divide-y divide-gray-200">
                    {dbData.data.map((row, i) => (
                      <tr key={i}>
                        {dbData.columns.map(column => (
                          <td 
                            key={column}
                            className="px-6 py-4 whitespace-nowrap text-sm text-gray-900"
                          >
                            {String(row[column])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
};

export default DatabaseMonitor;