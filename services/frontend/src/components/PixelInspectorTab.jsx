import React, { useState } from 'react';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export default function PixelInspectorTab({ sceneId }) {
  const [x, setX] = useState(72);
  const [y, setY] = useState(72);
  const [mlData, setMlData] = useState(null);
  const [mineralData, setMineralData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const inspect = async () => {
    if (!sceneId) return;
    setLoading(true);
    setError(null);
    setMlData(null);
    setMineralData(null);

    try {
      // ML parameters
      const mlResp = await fetch(
        `${API_URL}/ml/parameters/${sceneId}?x=${x}&y=${y}`
      );
      if (mlResp.ok) {
        setMlData(await mlResp.json());
      }

      // Mineral data
      const minResp = await fetch(
        `${API_URL}/mineral/pixel/${sceneId}?x=${x}&y=${y}`
      );
      if (minResp.ok) {
        setMineralData(await minResp.json());
      }
    } catch (err) {
      setError(err.message);
    }
    setLoading(false);
  };

  return (
    <div style={styles.container}>
      <h2 style={styles.title}>🔍 Pixel Inspector</h2>

      {/* Input Controls */}
      <div style={styles.card}>
        <div style={styles.controls}>
          <label style={styles.label}>
            Column (X):
            <input
              type="number"
              value={x}
              onChange={e => setX(parseInt(e.target.value) || 0)}
              min={0}
              max={999}
              style={styles.input}
            />
          </label>
          <label style={styles.label}>
            Row (Y):
            <input
              type="number"
              value={y}
              onChange={e => setY(parseInt(e.target.value) || 0)}
              min={0}
              max={999}
              style={styles.input}
            />
          </label>
          <button
            onClick={inspect}
            disabled={loading || !sceneId}
            style={styles.button}
          >
            {loading ? '⏳ Loading...' : '🔍 Inspect'}
          </button>
        </div>
      </div>

      {error && (
        <div style={{ ...styles.card, borderLeft: '4px solid #e74c3c' }}>
          <p style={{ color: '#e74c3c', margin: 0 }}>Error: {error}</p>
        </div>
      )}

      {/* Vegetation Results */}
      {mlData && (
        <div style={styles.card}>
          <h3 style={styles.cardTitle}>🌱 Vegetation Parameters</h3>
          <div style={styles.resultsGrid}>
            {mlData.parameters && Object.entries(mlData.parameters).map(
              ([key, val]) => (
                <div key={key} style={styles.resultItem}>
                  <span style={styles.resultLabel}>{key}</span>
                  <span style={styles.resultValue}>{val.toFixed(4)}</span>
                </div>
              )
            )}
            {mlData.indices && Object.entries(mlData.indices).map(
              ([key, val]) => (
                <div key={key} style={styles.resultItem}>
                  <span style={styles.resultLabel}>{key}</span>
                  <span style={{
                    ...styles.resultValue,
                    color: key === 'ndvi' ?
                      (val > 0.3 ? '#27ae60' : '#e74c3c') : '#2c3e50'
                  }}>
                    {val.toFixed(4)}
                  </span>
                </div>
              )
            )}
          </div>
        </div>
      )}

      {/* Mineral Results */}
      {mineralData && (
        <div style={styles.card}>
          <h3 style={styles.cardTitle}>🪨 Mineral Analysis</h3>

          <div style={styles.mineralHeader}>
            <span style={styles.mineralName}>{mineralData.mineral}</span>
            <span style={{
              ...styles.confidence,
              color: mineralData.confidence > 0.8 ? '#27ae60' :
                mineralData.confidence > 0.5 ? '#e67e22' : '#e74c3c'
            }}>
              {(mineralData.confidence * 100).toFixed(1)}% confidence
            </span>
          </div>

          {mineralData.abundances && (
            <div>
              <h4 style={{ margin: '15px 0 10px', color: '#7f8c8d', fontSize: '13px' }}>
                MINERAL COMPOSITION
              </h4>
              {Object.entries(mineralData.abundances)
                .filter(([_, v]) => v > 0.01)
                .sort((a, b) => b[1] - a[1])
                .map(([name, value]) => (
                  <div key={name} style={styles.abundanceRow}>
                    <span style={styles.abundanceName}>{name}</span>
                    <div style={styles.abundanceBarContainer}>
                      <div style={{
                        ...styles.abundanceBar,
                        width: `${value * 100}%`,
                        background: name === mineralData.mineral ?
                          '#3498db' : '#bdc3c7'
                      }} />
                    </div>
                    <span style={styles.abundanceValue}>
                      {(value * 100).toFixed(1)}%
                    </span>
                  </div>
                ))
              }
            </div>
          )}
        </div>
      )}

      {/* No data message */}
      {!loading && !mlData && !mineralData && sceneId && (
        <div style={styles.card}>
          <p style={{ color: '#95a5a6', textAlign: 'center' }}>
            Click "Inspect" to view pixel-level analysis
          </p>
        </div>
      )}

      {!sceneId && (
        <div style={styles.card}>
          <p style={{ color: '#95a5a6', textAlign: 'center' }}>
            Enter a Scene ID above to enable pixel inspection
          </p>
        </div>
      )}
    </div>
  );
}

const styles = {
  container: { padding: '20px' },
  title: { marginBottom: '20px', color: '#2c3e50' },
  card: {
    background: '#fff',
    borderRadius: '8px',
    padding: '20px',
    marginBottom: '16px',
    boxShadow: '0 1px 3px rgba(0,0,0,0.1)'
  },
  cardTitle: { margin: '0 0 15px 0', color: '#34495e', fontSize: '16px' },
  controls: { display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' },
  label: { display: 'flex', alignItems: 'center', gap: '8px', fontSize: '14px' },
  input: {
    width: '80px', padding: '8px', borderRadius: '4px',
    border: '1px solid #ddd', fontSize: '14px'
  },
  button: {
    padding: '10px 20px', borderRadius: '6px', border: 'none',
    background: '#3498db', color: '#fff', fontSize: '14px',
    cursor: 'pointer', fontWeight: '500'
  },
  resultsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
    gap: '12px'
  },
  resultItem: { display: 'flex', flexDirection: 'column' },
  resultLabel: { fontSize: '11px', color: '#95a5a6', textTransform: 'uppercase' },
  resultValue: { fontSize: '20px', fontWeight: 'bold', color: '#2c3e50' },
  mineralHeader: {
    display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '10px'
  },
  mineralName: {
    fontSize: '24px', fontWeight: 'bold', color: '#2c3e50',
    textTransform: 'capitalize'
  },
  confidence: { fontSize: '14px', fontWeight: '500' },
  abundanceRow: {
    display: 'flex', alignItems: 'center', gap: '12px',
    marginBottom: '8px'
  },
  abundanceName: {
    width: '120px', fontSize: '13px', color: '#34495e',
    textTransform: 'capitalize'
  },
  abundanceBarContainer: {
    flex: 1, height: '12px', background: '#ecf0f1',
    borderRadius: '6px', overflow: 'hidden'
  },
  abundanceBar: { height: '100%', borderRadius: '6px', transition: 'width 0.3s' },
  abundanceValue: { width: '50px', fontSize: '12px', color: '#7f8c8d', textAlign: 'right' }
};
