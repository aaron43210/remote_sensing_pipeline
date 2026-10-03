import React from 'react';
import { ArrowRight, Database, Server, Cpu, CloudRain, ShieldAlert } from 'lucide-react';

function ArchitectureTab() {
  return (
    <div className="animate-fade-in delay-100" style={{ padding: '2rem' }}>
      <header style={{ marginBottom: '2rem', textAlign: 'center' }}>
        <h1 style={{ fontSize: '2rem', color: 'var(--text-primary)', marginBottom: '0.5rem' }}>System Architecture</h1>
        <p style={{ color: 'var(--text-secondary)', fontSize: '1.1rem' }}>Data Flow and Microservices Processing Pipeline</p>
      </header>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem', alignItems: 'center' }}>

        {/* Tier 1: Ingestion */}
        <div className="glass-panel" style={{ width: '80%', maxWidth: '800px', padding: '2rem', textAlign: 'center', border: '1px solid var(--accent-primary)' }}>
          <Database size={48} color="var(--accent-primary)" style={{ margin: '0 auto 1rem auto' }} />
          <h3>1. Data Ingestion (NASA EMIT / Landsat)</h3>
          <p style={{ color: 'var(--text-secondary)' }}>Fetches raw Hyperspectral L1B data from NASA Earthdata based on user's 10km² bounding box.</p>
          <span style={{ fontSize: '0.75rem', fontWeight: 'bold', color: 'var(--text-primary)' }}>Developer: Ananthanarayanan</span>
        </div>

        <ArrowRight size={32} color="var(--text-secondary)" style={{ transform: 'rotate(90deg)' }} />

        {/* Tier 2: Kafka */}
        <div className="glass-panel" style={{ width: '80%', maxWidth: '800px', padding: '1.5rem', textAlign: 'center', background: 'var(--panel-border)' }}>
          <Server size={32} color="var(--text-primary)" style={{ margin: '0 auto 1rem auto' }} />
          <h3>Apache Kafka Event Bus</h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>Topics: `raw-data`, `preprocessed`, `analyzed`, `mineral-analyzed`, `thermal-processed`</p>
        </div>

        <ArrowRight size={32} color="var(--text-secondary)" style={{ transform: 'rotate(90deg)' }} />

        {/* Tier 3: Microservices */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1.5rem', width: '100%', maxWidth: '1200px' }}>

          <div className="glass-panel" style={{ textAlign: 'center', borderTop: '4px solid #22c55e' }}>
            <CloudRain size={32} color="#22c55e" style={{ margin: '0 auto 1rem auto' }} />
            <h4>Agriculture & Vegetation</h4>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Calculates NDVI, Red Edge, and runs heavy PROSAIL RTM inversions for LAI.</p>
            <span style={{ fontSize: '0.75rem', fontWeight: 'bold', color: 'var(--text-primary)' }}>Developer: Lanka Priya</span>
          </div>

          <div className="glass-panel" style={{ textAlign: 'center', borderTop: '4px solid #eab308' }}>
            <Database size={32} color="#eab308" style={{ margin: '0 auto 1rem auto' }} />
            <h4>Mineral Analysis</h4>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Matches pixel spectra against USGS library for geologic classification.</p>
            <span style={{ fontSize: '0.75rem', fontWeight: 'bold', color: 'var(--text-primary)' }}>Developer: Ananthan S & Harikrishanan S M</span>
          </div>

          <div className="glass-panel" style={{ textAlign: 'center', borderTop: '4px solid #ef4444' }}>
            <ShieldAlert size={32} color="#ef4444" style={{ margin: '0 auto 1rem auto' }} />
            <h4>Thermal Processing</h4>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Calculates Land Surface Temperature (LST) and Urban Heat Islands.</p>
            <span style={{ fontSize: '0.75rem', fontWeight: 'bold', color: 'var(--text-primary)' }}>Developer: Bainty Kaur Chugh</span>
          </div>

        </div>

        <ArrowRight size={32} color="var(--text-secondary)" style={{ transform: 'rotate(90deg)' }} />

        {/* Tier 4: ML Fusion */}
        <div className="glass-panel" style={{ width: '80%', maxWidth: '800px', padding: '2rem', textAlign: 'center', border: '1px solid var(--accent-secondary)' }}>
          <Cpu size={48} color="var(--accent-secondary)" style={{ margin: '0 auto 1rem auto' }} />
          <h3>4. ML Inference Fusion</h3>
          <p style={{ color: 'var(--text-secondary)' }}>Waits for all 3 microservices to complete, fuses their outputs, and runs the final Neural Network model to generate the composite prediction map.</p>
          <span style={{ fontSize: '0.75rem', fontWeight: 'bold', color: 'var(--text-primary)' }}>Developer: Aaron R (Project Lead)</span>
        </div>

      </div>
    </div>
  );
}

export default ArchitectureTab;
