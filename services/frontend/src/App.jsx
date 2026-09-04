import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer } from 'recharts';
import { Activity, Zap, HardDrive, DollarSign, RefreshCw, Server, Map, Layers } from 'lucide-react';
import MapViewer from './MapViewer';
import MineralAnalysis from './MineralAnalysis';
import MLComparisonTab from './components/MLComparisonTab';
import PixelInspectorTab from './components/PixelInspectorTab';
import './index.css';

const API_URL = "http://localhost:8000";

function App() {
  const [activeTab, setActiveTab] = useState('system');
  const [health, setHealth] = useState('Checking...');
  const [lastRefresh, setLastRefresh] = useState('');

  // Default dynamic state to 0 since nothing has processed yet
  const [metrics, setMetrics] = useState({
    total_scenes_processed: 0,
    avg_processing_time_seconds: 0.0,
    throughput: 0,
    cost_per_scene: 0.0000
  });

  const [performanceData, setPerformanceData] = useState([]);
  const [spectralData, setSpectralData] = useState([]);
  const [roiBounds, setRoiBounds] = useState(null);
  const [drawnBbox, setDrawnBbox] = useState(null);
  const [tasks, setTasks] = useState({ agriculture: true, mineral: false });
  const [processStatus, setProcessStatus] = useState('idle');
  const [liveStatusText, setLiveStatusText] = useState('');

  const fetchData = async () => {
    try {
      // 1. Check Health
      const healthRes = await axios.get(`${API_URL}/health`, { timeout: 2000 });
      setHealth(healthRes.data.status === 'healthy' ? 'Healthy' : 'Degraded');

      // 2. Fetch real metrics from backend (currently simulating empty response)
      // In production, this would hit: const metricsRes = await axios.get(`${API_URL}/metrics`);
      // Since pipeline hasn't processed data, we keep defaults at 0.
      
      // Example of setting bounding box if API returned one:
      // setRoiBounds([[40.4200, -87.0000], [40.4350, -86.9850]]);
      
    } catch (err) {
      setHealth('Unreachable');
    }
    setLastRefresh(new Date().toLocaleTimeString());
  };

  const handleDrawComplete = (bbox) => {
    // bbox is [lon_min, lat_min, lon_max, lat_max]
    setDrawnBbox(bbox);
    setRoiBounds([[bbox[1], bbox[0]], [bbox[3], bbox[2]]]);
  };

  const loadIdealTarget = () => {
    // California Central Valley / Sierra Nevada Foothills - rich in agriculture and minerals
    const bbox = [-120.2, 36.3, -119.2, 37.3];
    setDrawnBbox(bbox);
    setRoiBounds([[bbox[1], bbox[0]], [bbox[3], bbox[2]]]);
  };

  const handleRunPipeline = async () => {
    if (!drawnBbox) {
      alert("Please draw a bounding box on the map first!");
      return;
    }
    const selectedTasks = [];
    if (tasks.agriculture) selectedTasks.push("agriculture");
    if (tasks.mineral) selectedTasks.push("mineral");
    
    if (selectedTasks.length === 0) {
      alert("Please select at least one pipeline to run.");
      return;
    }

    setProcessStatus('processing');
    setLiveStatusText('STARTING');
    try {
      const payload = {
        bbox: {
          lon_min: drawnBbox[0],
          lat_min: drawnBbox[1],
          lon_max: drawnBbox[2],
          lat_max: drawnBbox[3]
        },
        tasks: selectedTasks
      };
      const res = await axios.post(`${API_URL}/process`, payload);
      const sceneId = res.data.scene_id;
      
      // Poll for live status updates from NASA download to ML Processing
      const pollInterval = setInterval(async () => {
        try {
          const statusRes = await axios.get(`${API_URL}/status/${sceneId}`);
          const currentStatus = statusRes.data.status;
          setLiveStatusText(currentStatus);
          
          if (currentStatus === 'COMPLETED' || currentStatus.startsWith('ERROR')) {
            clearInterval(pollInterval);
            setProcessStatus(currentStatus === 'COMPLETED' ? 'success' : 'error');
            if (currentStatus.startsWith('ERROR')) {
              alert("Processing Failed: " + currentStatus);
            }
            setTimeout(() => setProcessStatus('idle'), 5000);
          }
        } catch (e) {
          console.error("Polling error", e);
        }
      }, 1500);

    } catch (err) {
      console.error("Failed to start processing:", err);
      setProcessStatus('error');
      alert("Failed to fetch NASA data: " + (err.response?.data?.detail || err.message));
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000); // poll every 30s
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="app-container">
      <nav className="top-nav glass-panel animate-fade-in">
        <div className="top-nav-left">
          <Activity color="var(--accent-primary)" size={24} />
          <h2 style={{ margin: 0, color: 'var(--text-primary)', fontSize: '1.25rem' }}>
            Hyperspectral <span style={{ fontWeight: 400, color: 'var(--text-secondary)' }}>Production Pipeline</span>
          </h2>
          
          <div style={{ display: 'flex', marginLeft: '2rem', gap: '1rem' }}>
            <button 
              onClick={() => setActiveTab('system')}
              style={{
                background: 'transparent',
                border: 'none',
                color: activeTab === 'system' ? 'var(--accent-primary)' : 'var(--text-secondary)',
                fontWeight: activeTab === 'system' ? 600 : 400,
                borderBottom: activeTab === 'system' ? '2px solid var(--accent-primary)' : '2px solid transparent',
                padding: '0.5rem 0',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem'
              }}
            >
              <Activity size={16} /> System Overview
            </button>
            <button 
              onClick={() => setActiveTab('minerals')}
              style={{
                background: 'transparent',
                border: 'none',
                color: activeTab === 'minerals' ? 'var(--accent-tertiary)' : 'var(--text-secondary)',
                fontWeight: activeTab === 'minerals' ? 600 : 400,
                borderBottom: activeTab === 'minerals' ? '2px solid var(--accent-tertiary)' : '2px solid transparent',
                padding: '0.5rem 0',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem'
              }}
            >
              <Layers size={16} /> Mineral Analysis
            </button>
            <button 
              onClick={() => setActiveTab('ml')}
              style={{
                background: 'transparent',
                border: 'none',
                color: activeTab === 'ml' ? 'var(--accent-secondary)' : 'var(--text-secondary)',
                fontWeight: activeTab === 'ml' ? 600 : 400,
                borderBottom: activeTab === 'ml' ? '2px solid var(--accent-secondary)' : '2px solid transparent',
                padding: '0.5rem 0',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem'
              }}
            >
              <Zap size={16} /> ML Inference
            </button>
            <button 
              onClick={() => setActiveTab('pixel')}
              style={{
                background: 'transparent',
                border: 'none',
                color: activeTab === 'pixel' ? 'var(--accent-primary)' : 'var(--text-secondary)',
                fontWeight: activeTab === 'pixel' ? 600 : 400,
                borderBottom: activeTab === 'pixel' ? '2px solid var(--accent-primary)' : '2px solid transparent',
                padding: '0.5rem 0',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem'
              }}
            >
              <Activity size={16} /> Pixel Inspector
            </button>
          </div>
        </div>

        <div className="top-nav-right">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <Server size={18} color={health === 'Healthy' ? 'var(--accent-tertiary)' : 'var(--text-secondary)'} />
            <span style={{ color: health === 'Healthy' ? 'var(--accent-tertiary)' : 'var(--text-secondary)', fontWeight: 600 }}>
              System {health}
            </span>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginLeft: '0.5rem' }}>
              (Last updated: {lastRefresh})
            </span>
          </div>
          
          <button 
            onClick={fetchData}
            style={{
              padding: '0.5rem 1rem',
              borderRadius: '0', // Sharp rectangle
              border: 'none',
              background: 'var(--panel-bg)',
              color: 'var(--text-primary)',
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              border: '1px solid var(--panel-border)',
              marginRight: '1rem'
            }}
          >
            <RefreshCw size={16} /> Status
          </button>
          
          <div style={{ display: 'flex', gap: '1rem', marginRight: '1rem', alignItems: 'center' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-primary)', fontSize: '0.875rem' }}>
              <input type="checkbox" checked={tasks.agriculture} onChange={(e) => setTasks({...tasks, agriculture: e.target.checked})} disabled={processStatus === 'processing'} />
              Agriculture ML
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-primary)', fontSize: '0.875rem' }}>
              <input type="checkbox" checked={tasks.mineral} onChange={(e) => setTasks({...tasks, mineral: e.target.checked})} disabled={processStatus === 'processing'} />
              Minerals
            </label>
          </div>

          <button 
            onClick={handleRunPipeline}
            disabled={processStatus === 'processing'}
            style={{
              padding: '0.5rem 1.5rem',
              borderRadius: '4px', 
              border: 'none',
              background: processStatus === 'processing' ? 'var(--text-secondary)' : 'linear-gradient(to right, var(--accent-primary), var(--accent-secondary))',
              color: 'white',
              fontWeight: 600,
              cursor: processStatus === 'processing' ? 'not-allowed' : 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              boxShadow: '0 4px 6px -1px rgba(37, 99, 235, 0.2)'
            }}
          >
            <Map size={16} /> 
            {processStatus === 'processing' ? `Live Status: ${liveStatusText}` : 'Fetch Live Data & Process'}
          </button>
        </div>
      </nav>

      {/* Main Content */}
      <main className="main-content">
        {activeTab === 'system' && (
          <>
            <header className="animate-fade-in delay-100">
              <h1>Dashboard Overview</h1>
              <p>Real-time telemetry and architectural benchmarks.</p>
            </header>

            {/* Metrics Grid */}
            <section className="metrics-grid animate-fade-in delay-200">
              <div className="glass-panel">
                <div className="metric-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <HardDrive size={16} color="var(--accent-primary)" /> Scenes Processed
                </div>
                <div className="metric-value">{metrics.total_scenes_processed.toLocaleString()}</div>
              </div>
              
              <div className="glass-panel">
                <div className="metric-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <Zap size={16} color="var(--accent-secondary)" /> Avg Processing Time
                </div>
                <div className="metric-value">{metrics.avg_processing_time_seconds.toFixed(1)}s</div>
              </div>
              
              <div className="glass-panel">
                <div className="metric-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <Activity size={16} color="var(--accent-tertiary)" /> Throughput (hr)
                </div>
                <div className="metric-value">{metrics.throughput.toLocaleString()}</div>
              </div>
            </section>

            {/* Spatial Map View */}
            <section className="map-section animate-fade-in delay-200" style={{ height: '65vh', minHeight: '600px' }}>
              <div className="glass-panel" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <Map size={20} color="var(--accent-primary)" /> Spatial Output Viewer
                    <span style={{fontSize: '0.875rem', fontWeight: 400, color: 'var(--text-secondary)', marginLeft: '1rem'}}>
                      Draw a bounding box to fetch real NASA EMIT data for that region.
                    </span>
                  </div>
                  <button onClick={loadIdealTarget} style={{
                    padding: '0.25rem 0.75rem',
                    fontSize: '0.75rem',
                    backgroundColor: 'var(--panel-bg)',
                    border: '1px solid var(--accent-primary)',
                    color: 'var(--accent-primary)',
                    cursor: 'pointer',
                    borderRadius: '4px'
                  }}>
                    Load Ideal Target Area (California)
                  </button>
                </h3>
                <div style={{ flex: 1, minHeight: 0, borderRadius: '0', overflow: 'hidden' }}>
                  <MapViewer bounds={roiBounds} onDrawComplete={handleDrawComplete} />
                </div>
              </div>
            </section>

            {/* Charts Grid */}
            <section className="charts-grid animate-fade-in delay-300">
              {/* Performance Comparison */}
              <div className="glass-panel" style={{ height: '400px', display: 'flex', flexDirection: 'column' }}>
                <h3>Architecture Performance: Time (s)</h3>
                <div style={{ flex: 1, minHeight: 0, marginTop: '1rem', display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
                  {performanceData.length > 0 ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={performanceData} margin={{ top: 20, right: 30, left: 0, bottom: 5 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--panel-border)" vertical={false} />
                        <XAxis dataKey="name" stroke="var(--text-secondary)" />
                        <YAxis stroke="var(--text-secondary)" />
                        <RechartsTooltip 
                          contentStyle={{ backgroundColor: 'var(--panel-bg)', borderColor: 'var(--panel-border)', borderRadius: '8px', color: 'var(--text-primary)' }}
                        />
                        <Bar dataKey="time" name="Processing Time (s)" fill="var(--accent-primary)" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  ) : (
                    <p style={{ color: 'var(--text-secondary)' }}>Awaiting telemetry data...</p>
                  )}
                </div>
              </div>

              {/* Spectral Signature */}
              <div className="glass-panel" style={{ height: '400px', display: 'flex', flexDirection: 'column' }}>
                <h3>Live Spectral Signature</h3>
                <div style={{ flex: 1, minHeight: 0, marginTop: '1rem', display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
                  {spectralData.length > 0 ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={spectralData} margin={{ top: 20, right: 30, left: 0, bottom: 5 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--panel-border)" vertical={false} />
                        <XAxis 
                          dataKey="wavelength" 
                          stroke="var(--text-secondary)" 
                          label={{ value: 'Wavelength (nm)', position: 'insideBottomRight', offset: -5, fill: 'var(--text-secondary)' }} 
                        />
                        <YAxis 
                          stroke="var(--text-secondary)"
                          label={{ value: 'Reflectance', angle: -90, position: 'insideLeft', fill: 'var(--text-secondary)' }}
                        />
                        <RechartsTooltip 
                          contentStyle={{ backgroundColor: 'var(--panel-bg)', borderColor: 'var(--panel-border)', borderRadius: '8px', color: 'var(--text-primary)' }}
                        />
                        <Line 
                          type="monotone" 
                          dataKey="reflectance" 
                          stroke="var(--accent-tertiary)" 
                          strokeWidth={2}
                          dot={false}
                          activeDot={{ r: 6 }}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  ) : (
                    <p style={{ color: 'var(--text-secondary)' }}>Awaiting streaming spectra...</p>
                  )}
                </div>
              </div>
            </section>
          </>
        )}
        {activeTab === 'minerals' && <MineralAnalysis />}
        {activeTab === 'ml' && <MLComparisonTab sceneId="example_scene" />}
        {activeTab === 'pixel' && <PixelInspectorTab sceneId="example_scene" />}
      </main>
    </div>
  );
}

export default App;
