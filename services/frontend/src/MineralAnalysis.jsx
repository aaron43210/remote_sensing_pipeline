import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { Layers, Droplet, Search } from 'lucide-react';

const API_URL = "http://localhost:8000";

const MineralAnalysis = () => {
  const [library, setLibrary] = useState(null);
  const [sceneId, setSceneId] = useState("scene-001");
  const [pixelData, setPixelData] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // Fetch mineral library
    axios.get(`${API_URL}/mineral/library`).then(res => {
      setLibrary(res.data);
    }).catch(err => console.error("Could not load library", err));
  }, []);

  const inspectPixel = async () => {
    setLoading(true);
    try {
      // Hardcoded coordinates for demo, in reality clicking MapViewer would trigger this
      const res = await axios.get(`${API_URL}/mineral/pixel/${sceneId}?x=10&y=10`);
      setPixelData(res.data);
    } catch (err) {
      console.error(err);
    }
    setLoading(false);
  };

  const abundanceData = pixelData?.abundances 
    ? Object.entries(pixelData.abundances)
        .filter(([_, val]) => val > 0.01)
        .map(([name, val]) => ({ name, abundance: val * 100 }))
    : [];

  return (
    <div className="mineral-analysis-container animate-fade-in delay-100" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <header>
        <h1>Mineral Analysis</h1>
        <p>Hyperspectral mineral classification, unmixing, and pixel inspection.</p>
      </header>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '1.5rem' }}>
        
        {/* Library Info */}
        <div className="glass-panel">
          <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
            <Layers size={18} color="var(--accent-primary)" /> Spectral Library
          </h3>
          {library ? (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
                <span style={{ color: 'var(--text-secondary)' }}>Minerals Loaded:</span>
                <strong>{library.n_minerals}</strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-secondary)' }}>Spectral Range:</span>
                <strong>{library.wavelength_range[0]} - {library.wavelength_range[1]} nm</strong>
              </div>
              <div style={{ marginTop: '1rem', display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
                {library.minerals.slice(0, 8).map(m => (
                  <span key={m} style={{ padding: '0.2rem 0.5rem', background: 'rgba(37, 99, 235, 0.1)', color: 'var(--accent-primary)', fontSize: '0.75rem', fontWeight: 600 }}>
                    {m}
                  </span>
                ))}
                {library.minerals.length > 8 && <span style={{ padding: '0.2rem 0.5rem', color: 'var(--text-secondary)', fontSize: '0.75rem' }}>+ {library.minerals.length - 8} more</span>}
              </div>
            </div>
          ) : (
            <p style={{ color: 'var(--text-secondary)' }}>Loading library...</p>
          )}
        </div>

        {/* Pixel Inspector */}
        <div className="glass-panel">
          <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
            <Search size={18} color="var(--accent-secondary)" /> Pixel Inspector
          </h3>
          <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem' }}>
            <input 
              type="text" 
              value={sceneId} 
              onChange={e => setSceneId(e.target.value)}
              style={{ flex: 1, padding: '0.5rem', border: '1px solid var(--panel-border)', background: 'var(--background)', color: 'var(--text-primary)' }}
            />
            <button 
              onClick={inspectPixel}
              disabled={loading}
              style={{
                padding: '0.5rem 1rem',
                background: 'var(--accent-secondary)',
                color: 'white',
                border: 'none',
                cursor: loading ? 'not-allowed' : 'pointer',
                fontWeight: 600
              }}
            >
              {loading ? 'Inspecting...' : 'Inspect (10, 10)'}
            </button>
          </div>
          
          {pixelData && (
            <div>
              <div style={{ marginBottom: '1rem', padding: '1rem', background: 'rgba(16, 185, 129, 0.1)', borderLeft: '4px solid var(--accent-tertiary)' }}>
                <h4 style={{ margin: '0 0 0.5rem 0', color: 'var(--accent-tertiary)' }}>Dominant Mineral</h4>
                <strong style={{ fontSize: '1.25rem' }}>{pixelData.mineral.toUpperCase()}</strong>
                <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                  Confidence Score: {(pixelData.confidence * 100).toFixed(1)}%
                </div>
              </div>

              <h4 style={{ marginBottom: '0.5rem', color: 'var(--text-primary)' }}>Abundances</h4>
              {abundanceData.length > 0 ? (
                <div style={{ height: '200px' }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={abundanceData} layout="vertical" margin={{ top: 5, right: 20, left: 40, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" horizontal={true} vertical={false} stroke="var(--panel-border)" />
                      <XAxis type="number" domain={[0, 100]} hide />
                      <YAxis dataKey="name" type="category" axisLine={false} tickLine={false} tick={{ fill: 'var(--text-secondary)', fontSize: 12 }} />
                      <Tooltip 
                        contentStyle={{ backgroundColor: 'var(--panel-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
                        formatter={(val) => [`${val.toFixed(1)}%`, 'Abundance']}
                      />
                      <Bar dataKey="abundance" fill="var(--accent-tertiary)" radius={[0, 4, 4, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem' }}>No significant abundances found.</p>
              )}
            </div>
          )}
          {!pixelData && !loading && <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem' }}>Select a pixel on the map or click Inspect to view details.</p>}
        </div>

      </div>
    </div>
  );
};

export default MineralAnalysis;
