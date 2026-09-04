import React, { useState, useEffect } from 'react';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export default function MLComparisonTab({ sceneId }) {
  const [comparison, setComparison] = useState(null);
  const [mlResults, setMlResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (sceneId) {
      fetchData();
    }
  }, [sceneId]);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      // Fetch ML comparison
      const compResp = await fetch(`${API_URL}/ml/compare/${sceneId}`);
      if (compResp.ok) {
        setComparison(await compResp.json());
      }

      // Fetch ML results summary
      const mlResp = await fetch(`${API_URL}/ml/results/${sceneId}`);
      if (mlResp.ok) {
        setMlResults(await mlResp.json());
      }
    } catch (err) {
      setError(err.message);
    }
    setLoading(false);
  };

  if (!sceneId) {
    return (
      <div style={styles.container}>
        <p style={styles.placeholder}>Enter a Scene ID to view ML comparison</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={styles.container}>
        <p>Loading ML analysis...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div style={styles.container}>
        <p style={{ color: '#e74c3c' }}>Error: {error}</p>
      </div>
    );
  }

  const ml = comparison?.models?.ml || {};
  const physics = comparison?.models?.physics || {};

  return (
    <div style={styles.container}>
      <h2 style={styles.title}>🔬 Physics vs ML Comparison</h2>

      {/* Model Info Card */}
      <div style={styles.card}>
        <h3 style={styles.cardTitle}>ML Model: Physics-Guided Lightweight NN</h3>
        <div style={styles.infoGrid}>
          <div style={styles.infoItem}>
            <span style={styles.infoLabel}>Parameters:</span>
            <span style={styles.infoValue}>~10,000</span>
          </div>
          <div style={styles.infoItem}>
            <span style={styles.infoLabel}>Model Size:</span>
            <span style={styles.infoValue}>40 KB</span>
          </div>
          <div style={styles.infoItem}>
            <span style={styles.infoLabel}>Device:</span>
            <span style={styles.infoValue}>CPU Only</span>
          </div>
          <div style={styles.infoItem}>
            <span style={styles.infoLabel}>Speed:</span>
            <span style={styles.infoValue}>~1ms/pixel</span>
          </div>
        </div>
      </div>

      {/* Comparison Table */}
      <div style={styles.card}>
        <h3 style={styles.cardTitle}>📊 Side-by-Side Results</h3>
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Metric</th>
              <th style={styles.th}>Physics-Only</th>
              <th style={styles.th}>ML Hybrid</th>
              <th style={styles.th}>Difference</th>
            </tr>
          </thead>
          <tbody>
            <ComparisonRow
              label="Chlorophyll (μg/cm²)"
              physics={physics.mean_chlorophyll}
              ml={ml.mean_chlorophyll}
              format={v => v?.toFixed(1) || 'N/A'}
            />
            <ComparisonRow
              label="LAI"
              physics={physics.mean_lai}
              ml={ml.mean_lai}
              format={v => v?.toFixed(2) || 'N/A'}
            />
            <ComparisonRow
              label="NDVI"
              physics={null}
              ml={ml.mean_ndvi}
              format={v => v?.toFixed(3) || 'N/A'}
            />
            <ComparisonRow
              label="Processing Time (s)"
              physics={null}
              ml={ml.processing_time_sec}
              format={v => v?.toFixed(1) || 'N/A'}
            />
          </tbody>
        </table>
      </div>

      {/* ML Results Detail */}
      {mlResults && (
        <div style={styles.card}>
          <h3 style={styles.cardTitle}>🧠 ML Inference Details</h3>
          <div style={styles.infoGrid}>
            <div style={styles.infoItem}>
              <span style={styles.infoLabel}>Classes Predicted:</span>
              <span style={styles.infoValue}>
                {mlResults.n_classes_predicted || 'N/A'}
              </span>
            </div>
            <div style={styles.infoItem}>
              <span style={styles.infoLabel}>Pixels Processed:</span>
              <span style={styles.infoValue}>
                {(mlResults.timing?.pixels_processed || 0).toLocaleString()}
              </span>
            </div>
            <div style={styles.infoItem}>
              <span style={styles.infoLabel}>ms/pixel:</span>
              <span style={styles.infoValue}>
                {(mlResults.timing?.ms_per_pixel || 0).toFixed(2)}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Physics Features Used */}
      <div style={styles.card}>
        <h3 style={styles.cardTitle}>⚗️ Physics Features (No Learning)</h3>
        <div style={styles.featureList}>
          {[
            'NDVI', 'Red Edge Position', 'Red Edge Slope',
            'Chlorophyll Absorption Depth', 'Water Absorption Depth',
            'NIR Mean Reflectance', 'SWIR Mean Reflectance', 'Brightness'
          ].map((f, i) => (
            <span key={i} style={styles.featureBadge}>{f}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

function ComparisonRow({ label, physics, ml, format }) {
  const diff = (physics != null && ml != null) ? ml - physics : null;
  const diffColor = diff == null ? '#666' :
    Math.abs(diff) < 0.05 ? '#27ae60' : '#e67e22';

  return (
    <tr>
      <td style={styles.td}>{label}</td>
      <td style={styles.td}>{format(physics)}</td>
      <td style={styles.td}>{format(ml)}</td>
      <td style={{ ...styles.td, color: diffColor }}>
        {diff != null ? `${diff > 0 ? '+' : ''}${format(diff)}` : '—'}
      </td>
    </tr>
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
  infoGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
    gap: '12px'
  },
  infoItem: { display: 'flex', flexDirection: 'column' },
  infoLabel: { fontSize: '12px', color: '#7f8c8d', marginBottom: '4px' },
  infoValue: { fontSize: '18px', fontWeight: 'bold', color: '#2c3e50' },
  table: { width: '100%', borderCollapse: 'collapse' },
  th: {
    textAlign: 'left', padding: '10px', borderBottom: '2px solid #eee',
    color: '#7f8c8d', fontSize: '12px'
  },
  td: { padding: '10px', borderBottom: '1px solid #f0f0f0', fontSize: '14px' },
  featureList: { display: 'flex', flexWrap: 'wrap', gap: '8px' },
  featureBadge: {
    background: '#eaf2f8', color: '#2980b9', padding: '6px 12px',
    borderRadius: '16px', fontSize: '12px', fontWeight: '500'
  },
  placeholder: { color: '#95a5a6', textAlign: 'center', padding: '40px' }
};
