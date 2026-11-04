import { useEffect, useRef } from 'react'
import L from 'leaflet'
import '@geoman-io/leaflet-geoman-free'

const SATELLITE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
const STYLE = { color: '#d9f2b4', weight: 2.5, fillColor: '#d9f2b4', fillOpacity: 0.15 }

/**
 * Satellite map. In edit mode the user draws ONE polygon; onChange gets GeoJSON (or null).
 * In view mode it shows `geometry` and an optional `overlayUrl` tile layer (GEE loss pixels).
 */
export default function FarmMap({ editable = false, geometry, onChange, overlayUrl, label, height = 460 }) {
  const el = useRef(null)
  const mapRef = useRef(null)
  const layerRef = useRef(null)

  useEffect(() => {
    const map = L.map(el.current, { zoomControl: true }).setView([5.808, -2.4335], 15)
    mapRef.current = map
    L.tileLayer(SATELLITE, {
      maxZoom: 19,
      attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
    }).addTo(map)

    if (editable) {
      map.pm.addControls({
        position: 'topleft',
        drawMarker: false, drawCircleMarker: false, drawPolyline: false,
        drawRectangle: false, drawCircle: false, drawText: false,
        cutPolygon: false, rotateMode: false, dragMode: false,
      })
      map.pm.setPathOptions(STYLE)

      const emit = () => onChange?.(layerRef.current ? layerRef.current.toGeoJSON().geometry : null)
      map.on('pm:create', (e) => {
        if (layerRef.current) map.removeLayer(layerRef.current) // only one farm boundary
        layerRef.current = e.layer
        e.layer.on('pm:edit', emit)
        emit()
      })
      map.on('pm:remove', () => { layerRef.current = null; emit() })
    }
    return () => { map.remove(); mapRef.current = null; layerRef.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Show an existing geometry (view mode, or a restored draft in edit mode)
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (!geometry) {
      if (layerRef.current) { map.removeLayer(layerRef.current); layerRef.current = null }
      return
    }
    if (layerRef.current) {
      const current = JSON.stringify(layerRef.current.toGeoJSON().geometry.coordinates)
      if (current === JSON.stringify(geometry.coordinates)) return
      map.removeLayer(layerRef.current)
    }
    const layer = L.geoJSON(geometry, { style: STYLE }).getLayers()[0]
    layer.addTo(map)
    if (label) layer.bindTooltip(label, { permanent: true, direction: 'center', className: 'map-label' })
    if (editable) layer.on('pm:edit', () => onChange?.(layer.toGeoJSON().geometry))
    layerRef.current = layer
    map.fitBounds(layer.getBounds(), { padding: [40, 40] })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geometry, label])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !overlayUrl) return
    const overlay = L.tileLayer(overlayUrl, { opacity: 0.85 }).addTo(map)
    return () => map.removeLayer(overlay)
  }, [overlayUrl])

  const locate = () => {
    navigator.geolocation?.getCurrentPosition(
      (p) => mapRef.current.setView([p.coords.latitude, p.coords.longitude], 17),
      () => alert('Could not get GPS position.'),
      { enableHighAccuracy: true },
    )
  }

  return (
    <div className="map-wrap" style={{ height }}>
      <div ref={el} className="map" />
      {editable && <button type="button" className="map-locate" onClick={locate}>◎ My GPS location</button>}
    </div>
  )
}
