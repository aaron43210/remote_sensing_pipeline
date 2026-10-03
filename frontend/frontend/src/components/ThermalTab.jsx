import React from 'react';
import MapViewer from '../MapViewer';

function ThermalTab({ bounds, onDrawComplete }) {
  return (
    <div className="animate-fade-in delay-100" style={{ height: 'calc(100vh - 100px)' }}>
      <header style={{ paddingBottom: '1rem' }}>
        <h1 style={{ fontSize: '1.5rem', margin: 0 }}>Thermal Analytics</h1>
        <p style={{ color: 'var(--text-secondary)', margin: '0.25rem 0 0 0' }}>Land Surface Temperature (LST) & Urban Heat Islands</p>
      </header>
      
      <div className="glass-panel" style={{ height: 'calc(100% - 80px)', display: 'flex', flexDirection: 'column', padding: '0.5rem' }}>
        <div style={{ flex: 1, minHeight: 0, borderRadius: '4px', overflow: 'hidden' }}>
          <MapViewer bounds={bounds} onDrawComplete={onDrawComplete} />
        </div>
      </div>
    </div>
  );
}

export default ThermalTab;
