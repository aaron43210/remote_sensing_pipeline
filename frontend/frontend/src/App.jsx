import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer } from 'recharts';
import { Activity, Zap, HardDrive, Server, Map, Layers, RefreshCw, Cpu } from 'lucide-react';
import MapViewer from './MapViewer';
import MineralAnalysis from './MineralAnalysis';
import AgricultureTab from './components/AgricultureTab';
import ThermalTab from './components/ThermalTab';
import ArchitectureTab from './components/ArchitectureTab';
import './index.css';

const API_URL = "http://localhost:8000";

function App() {
  const [activeTab, setActiveTab] = useState('system');
  const [health, setHealth] = useState('Checking...');
  const [lastRefresh, setLastRefresh] = useState('');

  const [metrics, setMetrics] = useState({
    total_scenes_processed: 0,
    avg_processing_time_seconds: 0.0,
    throughput: 0,
  });

  const [performanceData, setPerformanceData] = useState([]);
  const [spectralData, setSpectralData] = useState([]);
  const [roiBounds, setRoiBounds] = useState(null);
  const [drawnBbox, setDrawnBbox] = useState(null);
  const [tasks, setTasks] = useState({ agriculture: true, mineral: false, thermal: false });
  const [layers, setLayers] = useState({ agriculture: false, mineral: false, thermal: false });
  const [processStatus, setProcessStatus] = useState('idle');
  const [liveStatusText, setLiveStatusText] = useState('');
  const [progressPercent, setProgressPercent] = useState(0);

  const fetchData = async () => {
    try {
      const healthRes = await axios.get(`${API_URL}/health`, { timeout: 2000 });
      setHealth(healthRes.data.status === 'healthy' ? 'Healthy' : 'Degraded');
    } catch (err) {
      setHealth('Unreachable');
    }
    setLastRefresh(new Date().toLocaleTimeString());
  };

  const handleDrawComplete = (bbox) => {
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
    if (tasks.thermal) selectedTasks.push("thermal");

    if (selectedTasks.length === 0) {
      alert("Please select at least one pipeline to run.");
      return;
    }

    setProcessStatus('processing');
    setLiveStatusText('STARTING NASA DOWNLOAD');
    setProgressPercent(10);

    try {
      const payload = {
        bbox: { lon_min: drawnBbox[0], lat_min: drawnBbox[1], lon_max: drawnBbox[2], lat_max: drawnBbox[3] },
        tasks: selectedTasks
      };

      const res = await axios.post(`${API_URL}/process`, payload);
      const sceneId = res.data.scene_id;

      let currentProg = 10;
      const pollInterval = setInterval(async () => {
        try {
          const statusRes = await axios.get(`${API_URL}/status/${sceneId}`);
          const currentStatus = statusRes.data.status;
          setLiveStatusText(currentStatus);

          if (currentProg < 90) {
            currentProg += 15;
            setProgressPercent(currentProg);
          }

          if (currentStatus === 'COMPLETED' || currentStatus.startsWith('ERROR')) {
            clearInterval(pollInterval);
            setProgressPercent(100);
            setProcessStatus(currentStatus === 'COMPLETED' ? 'success' : 'error');

            if (currentStatus === 'COMPLETED') {
              // Auto-enable layers for tasks that were selected
              setLayers({
                agriculture: selectedTasks.includes('agriculture'),
                mineral: selectedTasks.includes('mineral'),
                thermal: selectedTasks.includes('thermal')
              });
            }

            if (currentStatus.startsWith('ERROR')) {
              alert("Processing Failed: " + currentStatus);
            }
            setTimeout(() => {
              setProcessStatus(currentStatus === 'COMPLETED' ? 'success' : 'idle');
              setProgressPercent(0);
            }, 3000);
          }
        } catch (e) {
          console.error("Polling error", e);
        }
      }, 1500);

    } catch (err) {
      console.error("Failed to start processing:", err);
      setProcessStatus('error');
      setProgressPercent(0);
      alert("Failed to fetch NASA data: " + (err.response?.data?.detail || err.message));
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="app-container">
      <nav className="top-nav glass-panel animate-fade-in" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'nowrap', padding: '1rem 2rem' }}>
        <div className="top-nav-left" style={{ display: 'flex', alignItems: 'center', gap: '2rem' }}>

          {/* Logo & Title */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', whiteSpace: 'nowrap' }}>
            <Activity color="var(--accent-primary)" size={32} />
            <h2 style={{ margin: 0, color: 'var(--text-primary)', fontSize: '1.25rem', display: 'flex', flexDirection: 'column' }}>
              Hyperspectral & Thermal<span style={{ fontWeight: 400, color: 'var(--text-secondary)', fontSize: '1rem' }}>& Remote Sensing Pipeline</span>
            </h2>
          </div>

          {/* Tabs */}
          <div style={{ display: 'flex', gap: '1.5rem', borderLeft: '1px solid var(--panel-border)', paddingLeft: '1.5rem' }}>
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
              System Overview
            </button>
            <button
              onClick={() => setActiveTab('agriculture')}
              style={{
                background: 'transparent',
                border: 'none',
                color: activeTab === 'agriculture' ? '#22c55e' : 'var(--text-secondary)',
                fontWeight: activeTab === 'agriculture' ? 600 : 400,
                borderBottom: activeTab === 'agriculture' ? '2px solid #22c55e' : '2px solid transparent',
                padding: '0.5rem 0',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem'
              }}
            >
              Agriculture
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
              Mineral Analysis
            </button>
            <button
              onClick={() => setActiveTab('thermal')}
              style={{
                background: 'transparent',
                border: 'none',
                color: activeTab === 'thermal' ? '#ef4444' : 'var(--text-secondary)',
                fontWeight: activeTab === 'thermal' ? 600 : 400,
                borderBottom: activeTab === 'thermal' ? '2px solid #ef4444' : '2px solid transparent',
                padding: '0.5rem 0',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                whiteSpace: 'nowrap'
              }}
            >
              Thermal
            </button>
            <button
              onClick={() => setActiveTab('architecture')}
              style={{
                background: 'transparent',
                border: 'none',
                color: activeTab === 'architecture' ? 'var(--accent-secondary)' : 'var(--text-secondary)',
                fontWeight: activeTab === 'architecture' ? 600 : 400,
                borderBottom: activeTab === 'architecture' ? '2px solid var(--accent-secondary)' : '2px solid transparent',
                padding: '0.5rem 0',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                whiteSpace: 'nowrap'
              }}
            >
              Architecture
            </button>
          </div>
        </div>

        <div className="top-nav-right" style={{ display: 'flex', alignItems: 'center' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', whiteSpace: 'nowrap', margin: '0 2rem' }}>
            <Server size={18} color={health === 'Healthy' ? 'var(--accent-tertiary)' : 'var(--text-secondary)'} />
            <span style={{ color: health === 'Healthy' ? 'var(--accent-tertiary)' : 'var(--text-secondary)', fontWeight: 600 }}>
              System {health}
            </span>
          </div>

          <div style={{ display: 'flex', gap: '1rem', marginRight: '1rem', marginLeft: '1rem', alignItems: 'center' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-primary)', fontSize: '0.875rem' }}>
              <input type="checkbox" checked={tasks.agriculture} onChange={(e) => setTasks({ ...tasks, agriculture: e.target.checked })} disabled={processStatus === 'processing'} />
              Agriculture (PROSAIL)
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-primary)', fontSize: '0.875rem' }}>
              <input type="checkbox" checked={tasks.mineral} onChange={(e) => setTasks({ ...tasks, mineral: e.target.checked })} disabled={processStatus === 'processing'} />
              Minerals
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-primary)', fontSize: '0.875rem' }}>
              <input type="checkbox" checked={tasks.thermal} onChange={(e) => setTasks({ ...tasks, thermal: e.target.checked })} disabled={processStatus === 'processing'} />
              Thermal
            </label>
          </div>

          {processStatus === 'processing' ? (
            <div style={{
              width: '250px',
              background: 'var(--panel-bg)',
              borderRadius: '4px',
              border: '1px solid var(--panel-border)',
              position: 'relative',
              overflow: 'hidden',
              height: '36px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              <div style={{
                position: 'absolute',
                left: 0,
                top: 0,
                bottom: 0,
                width: `${progressPercent}%`,
                background: 'linear-gradient(to right, var(--accent-primary), var(--accent-secondary))',
                transition: 'width 0.5s ease-in-out',
                opacity: 0.3
              }}></div>
              <span style={{ position: 'relative', zIndex: 1, fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                {liveStatusText} ({progressPercent}%)
              </span>
            </div>
          ) : (
            <button
              onClick={handleRunPipeline}
              style={{
                padding: '0.5rem 1.5rem',
                borderRadius: '4px',
                border: 'none',
                background: 'linear-gradient(to right, var(--accent-primary), var(--accent-secondary))',
                color: 'white',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                boxShadow: '0 4px 6px -1px rgba(37, 99, 235, 0.2)'
              }}
            >
              <Map size={16} /> Fetch Live Data & Process
            </button>
          )}
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
            <section className="map-section animate-fade-in delay-200" style={{ height: '600px', marginTop: '1.5rem' }}>
              <div className="glass-panel" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <Map size={20} color="var(--accent-primary)" /> Spatial Target Selection
                  </div>

                  {/* Layer Toggles (Only show if processing is success) */}
                  {processStatus === 'success' && (
                    <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
                      <span style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>Overlay Results:</span>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.875rem', color: '#22c55e' }}>
                        <input type="checkbox" checked={layers.agriculture} onChange={(e) => setLayers({ ...layers, agriculture: e.target.checked })} />
                        Agriculture
                      </label>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.875rem', color: '#eab308' }}>
                        <input type="checkbox" checked={layers.mineral} onChange={(e) => setLayers({ ...layers, mineral: e.target.checked })} />
                        Minerals
                      </label>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.875rem', color: '#ef4444' }}>
                        <input type="checkbox" checked={layers.thermal} onChange={(e) => setLayers({ ...layers, thermal: e.target.checked })} />
                        Thermal
                      </label>
                    </div>
                  )}
                </h3>
                <div style={{ flex: 1, minHeight: 0, borderRadius: '4px', overflow: 'hidden', border: '1px solid var(--panel-border)' }}>
                  <MapViewer bounds={roiBounds} onDrawComplete={handleDrawComplete} layers={layers} />
                </div>
              </div>
            </section>

            {/* Charts Grid - Expanded since map is gone */}
            <section style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem', marginTop: '1.5rem' }} className="animate-fade-in delay-300">
              {/* Performance Comparison */}
              <div className="glass-panel" style={{ height: '500px', display: 'flex', flexDirection: 'column' }}>
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
              <div className="glass-panel" style={{ height: '500px', display: 'flex', flexDirection: 'column' }}>
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
        {activeTab === 'agriculture' && <AgricultureTab bounds={roiBounds} onDrawComplete={handleDrawComplete} />}
        {activeTab === 'minerals' && <MineralAnalysis bounds={roiBounds} onDrawComplete={handleDrawComplete} />}
        {activeTab === 'thermal' && <ThermalTab bounds={roiBounds} onDrawComplete={handleDrawComplete} />}
        {activeTab === 'architecture' && <ArchitectureTab />}
      </main>
    </div>
  );
}

export default App;
