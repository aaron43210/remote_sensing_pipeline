import React from 'react';
import MapViewer from '../MapViewer';

function AgricultureTab({ bounds, onDrawComplete }) {
  return (
    <div className="animate-fade-in delay-100" style={{ height: 'calc(100vh - 100px)' }}>
      <header style={{ paddingBottom: '1rem' }}>
        <h1 style={{ fontSize: '1.5rem', margin: 0 }}>Agriculture & Vegetation Analytics</h1>
        <p style={{ color: 'var(--text-secondary)', margin: '0.25rem 0 0 0' }}>Spatial NDVI & PROSAIL Inversion Maps</p>
      </header>
      
      <div className="glass-panel" style={{ height: 'calc(100% - 80px)', display: 'flex', flexDirection: 'column', padding: '0.5rem' }}>
        <div style={{ flex: 1, minHeight: 0, borderRadius: '4px', overflow: 'hidden' }}>
          <MapViewer bounds={bounds} onDrawComplete={onDrawComplete} />
        </div>
      </div>
    </div>
  );
}

export default AgricultureTab;
