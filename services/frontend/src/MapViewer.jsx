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
        let lat = e.latlng.lat;
        let lng = e.latlng.lng;
        
        // Hard limit to ~10km (0.1 degrees)
        const maxDiff = 0.1;
        if (Math.abs(lat - startPoint.lat) > maxDiff) {
          lat = startPoint.lat + (Math.sign(lat - startPoint.lat) * maxDiff);
        }
        if (Math.abs(lng - startPoint.lng) > maxDiff) {
          lng = startPoint.lng + (Math.sign(lng - startPoint.lng) * maxDiff);
        }
        
        const constrainedLatLng = L.latLng(lat, lng);
        setCurrentBounds([startPoint, constrainedLatLng]);
      }
    },
    mouseup(e) {
      if (startPoint && currentBounds) {
        map.dragging.enable();
        // Use the constrained bounds rather than where the mouse physically ended up
        const bounds = L.latLngBounds(currentBounds[0], currentBounds[1]);
        
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

const MapViewer = ({ bounds, onDrawComplete, layers }) => {
  // Center map on California Central Valley (fixed for EMIT + Landsat calibration + Agriculture/Minerals)
  const center = [36.7783, -119.4179]; 
  const zoom = 9;
  
  // Strictly lock map panning to the Central Valley / Sierra Nevada region
  const maxBounds = [
    [35.0, -120.5], // Southwest bound
    [38.5, -118.0]  // Northeast bound
  ];

  return (
    <div style={{ height: '100%', width: '100%', position: 'relative' }}>
      <MapContainer 
        center={center} 
        zoom={zoom} 
        maxBounds={maxBounds}
        maxBoundsViscosity={1.0}
        minZoom={7}
        style={{ height: '100%', width: '100%', backgroundColor: '#e5e7eb', cursor: 'crosshair' }}
        scrollWheelZoom={true}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.esri.com/">Esri</a>'
          url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        />
        
        {/* Custom Drag Tool */}
        <DragBoundingBox onDrawComplete={onDrawComplete} />

        {/* The Final Set Bounds */}
        {bounds && (
          <>
            <Rectangle bounds={bounds} pathOptions={{ color: 'var(--accent-primary)', weight: 3, fillOpacity: 0.1 }}>
              <LeafletTooltip direction="top" permanent>
                Selected Target Area
              </LeafletTooltip>
            </Rectangle>
            
            {/* Mock Overlays when layers are toggled */}
            {layers?.agriculture && (
              <Rectangle bounds={bounds} pathOptions={{ color: '#22c55e', stroke: false, fillOpacity: 0.4 }} />
            )}
            {layers?.mineral && (
              <Rectangle bounds={bounds} pathOptions={{ color: '#eab308', stroke: false, fillOpacity: 0.4 }} />
            )}
            {layers?.thermal && (
              <Rectangle bounds={bounds} pathOptions={{ color: '#ef4444', stroke: false, fillOpacity: 0.4 }} />
            )}
          </>
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
        {bounds ? "Area selected. Local processing limited to 10km² max." : "Click and Drag on the map to select an area (Max 10km²)"}
      </div>

    </div>
  );
};

export default MapViewer;
