import { useEffect, useRef } from 'react'
import L from 'leaflet'
import '@geoman-io/leaflet-geoman-free'

const SATELLITE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
const STYLE = { color: '#d9f2b4', weight: 2.5, fillColor: '#d9f2b4', fillOpacity: 0.15 }

/**
 * Satellite map. In edit mode the user draws ONE polygon; onChange gets GeoJSON (or null).
 * In view mode it shows `geometry` and an optional `overlayUrl` tile layer (GEE loss pixels).
 * In walk mode (mode="walk") it shows a live GPS dot + accuracy circle (`livePosition`) and the
 * provisional path/polygon built from `walkPoints` -- no Geoman controls, not editable.
 */
export default function FarmMap({
  editable = false, geometry, onChange, overlayUrl, label, boundaryStyle, height = 460,
  mode, walkPoints, livePosition,
}) {
  const el = useRef(null)
  const mapRef = useRef(null)
  const layerRef = useRef(null)
  const walkLayerRef = useRef(null)
  const liveLayerRef = useRef(null)

  useEffect(() => {
    const map = L.map(el.current, { zoomControl: true }).setView([5.808, -2.4335], 15)
    mapRef.current = map
    L.tileLayer(SATELLITE, {
      maxZoom: 19, maxNativeZoom: 17, // Esri imagery tops out at 17 -- upscale rather than show blank tiles
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
    const layer = L.geoJSON(geometry, { style: boundaryStyle || STYLE }).getLayers()[0]
    layer.addTo(map)
    if (label) {
      layer.bindTooltip(label, {
        permanent: true, direction: 'top', offset: [0, -6], className: 'map-label',
      })
    }
    if (editable) layer.on('pm:edit', () => onChange?.(layer.toGeoJSON().geometry))
    layerRef.current = layer
    map.fitBounds(layer.getBounds(), { padding: [40, 40], maxZoom: 17 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geometry, label, boundaryStyle])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !overlayUrl) return
    const overlay = L.tileLayer(overlayUrl, { opacity: 0.85 }).addTo(map)
    return () => map.removeLayer(overlay)
  }, [overlayUrl])

  // Walk mode: provisional path/polygon + a marker per captured point
  useEffect(() => {
    const map = mapRef.current
    if (!map || mode !== 'walk') return
    if (walkLayerRef.current) { map.removeLayer(walkLayerRef.current); walkLayerRef.current = null }
    if (!walkPoints?.length) return
    const latlngs = walkPoints.map((p) => [p.lat, p.lon])
    const group = L.layerGroup()
    const shape = walkPoints.length >= 3
      ? L.polygon(latlngs, { color: '#ffe034', weight: 3, fillColor: '#ffe034', fillOpacity: 0.1 })
      : L.polyline(latlngs, { color: '#ffe034', weight: 3 })
    shape.addTo(group)
    walkPoints.forEach((p) => {
      L.circleMarker([p.lat, p.lon], {
        radius: 6, color: '#fff', weight: 2, fillColor: p.flagged ? '#c49a2c' : '#2f7d3a', fillOpacity: 1,
      }).addTo(group)
    })
    group.addTo(map)
    walkLayerRef.current = group
    if (latlngs.length === 1) map.setView(latlngs[0], 18)
    else map.fitBounds(shape.getBounds(), { padding: [50, 50], maxZoom: 17 })
  }, [mode, walkPoints])

  // Walk mode: live GPS dot + accuracy circle
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (liveLayerRef.current) { map.removeLayer(liveLayerRef.current); liveLayerRef.current = null }
    if (!livePosition) return
    const group = L.layerGroup()
    L.circle([livePosition.lat, livePosition.lon], {
      radius: livePosition.accuracy, color: '#2f6fed', weight: 1, fillColor: '#2f6fed', fillOpacity: 0.12,
    }).addTo(group)
    L.circleMarker([livePosition.lat, livePosition.lon], {
      radius: 6, color: '#fff', weight: 2, fillColor: '#2f6fed', fillOpacity: 1,
    }).addTo(group)
    group.addTo(map)
    liveLayerRef.current = group
    if (!walkPoints?.length) map.setView([livePosition.lat, livePosition.lon], Math.max(map.getZoom(), 17))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [livePosition])

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
