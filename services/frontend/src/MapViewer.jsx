import React, { useState } from 'react';
import { MapContainer, TileLayer, Rectangle, Tooltip as LeafletTooltip, useMapEvents } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

// Fix for default Leaflet icon paths in React
import L from 'leaflet';
import icon from 'leaflet/dist/images/marker-icon.png';
import iconShadow from 'leaflet/dist/images/marker-shadow.png';
let DefaultIcon = L.icon({
    iconUrl: icon,
    shadowUrl: iconShadow
});
L.Marker.prototype.options.icon = DefaultIcon;

// Custom Drag-to-Draw Bounding Box Tool
const DragBoundingBox = ({ onDrawComplete }) => {
  const [startPoint, setStartPoint] = useState(null);
  const [currentBounds, setCurrentBounds] = useState(null);

  const map = useMapEvents({
    mousedown(e) {
      // Disable map panning while drawing
      map.dragging.disable();
      setStartPoint(e.latlng);
      setCurrentBounds([e.latlng, e.latlng]);
    },
    mousemove(e) {
      if (startPoint) {
        setCurrentBounds([startPoint, e.latlng]);
      }
    },
    mouseup(e) {
      if (startPoint) {
        map.dragging.enable();
        const bounds = L.latLngBounds(startPoint, e.latlng);
        
        // Convert to [lon_min, lat_min, lon_max, lat_max]
        const bbox = [
          bounds.getWest(),
          bounds.getSouth(),
          bounds.getEast(),
          bounds.getNorth()
        ];
        
        if (onDrawComplete) {
          onDrawComplete(bbox);
        }
        setStartPoint(null);
        setCurrentBounds(null); // Let the parent component render the final rectangle
      }
    }
  });

  // Render the temporary rectangle while the user is dragging their mouse
  return currentBounds ? (
    <Rectangle bounds={currentBounds} pathOptions={{ color: 'var(--accent-secondary)', weight: 2, dashArray: '5, 5', fillOpacity: 0.2 }} />
  ) : null;
};

const MapViewer = ({ bounds, onDrawComplete }) => {
  // Center map on California (a known EMIT hotspot)
  const center = [36.7783, -119.4179]; 
  const zoom = 6;
  
  // Restrict map panning to US/North America to prevent wandering into empty ocean
  const maxBounds = [
    [20.0, -130.0], // Southwest
    [50.0, -65.0]  // Northeast
  ];

  return (
    <div style={{ height: '100%', width: '100%', position: 'relative' }}>
      <MapContainer 
        center={center} 
        zoom={zoom} 
        maxBounds={maxBounds}
        maxBoundsViscosity={1.0}
        minZoom={4}
        style={{ height: '100%', width: '100%', backgroundColor: '#e5e7eb', cursor: 'crosshair' }}
        scrollWheelZoom={true}
      >
        <TileLayer
          attribution='&copy; <a href="https://carto.com/">CARTO</a>'
          url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
        />
        
        {/* Custom Drag Tool */}
        <DragBoundingBox onDrawComplete={onDrawComplete} />

        {/* The Final Set Bounds */}
        {bounds && (
          <Rectangle bounds={bounds} pathOptions={{ color: 'var(--accent-primary)', weight: 3, fillOpacity: 0.1 }}>
            <LeafletTooltip direction="top" permanent>
              Selected Target Area
            </LeafletTooltip>
          </Rectangle>
        )}
      </MapContainer>

      {/* Helper Text Overlay */}
      <div style={{
        position: 'absolute',
        top: '10px',
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 1000,
        backgroundColor: 'rgba(0, 0, 0, 0.7)',
        color: 'white',
        padding: '0.5rem 1rem',
        borderRadius: '20px',
        fontSize: '0.875rem',
        pointerEvents: 'none'
      }}>
        Click and Drag on the map to draw a Bounding Box
      </div>

    </div>
  );
};

export default MapViewer;
