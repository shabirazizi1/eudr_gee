import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import L from 'leaflet'
import { addReferenceLayers } from './mapLayers.js'

const SATELLITE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'

// Same palette as the risk badges / distribution bar in styles.css
const COLORS = { Low: '#2f7d3a', Medium: '#c49a2c', High: '#b23b2e' }
const UNSCREENED = '#8a8174'
const LABELS = { Low: 'Low risk', Medium: 'Medium risk', High: 'High risk', none: 'Not screened' }

const levelKey = (farm) => (farm.risk_level in COLORS ? farm.risk_level : 'none')
const colorOf = (farm) => COLORS[farm.risk_level] || UNSCREENED

/**
 * Portfolio-wide risk map: one polygon per farm, filled by satellite risk level.
 * Clicking a plot opens its farm page. The legend counts follow the currently
 * filtered list, so it always matches what is drawn.
 */
export default function RiskMap({ farms, height = 400 }) {
  const el = useRef(null)
  const mapRef = useRef(null)
  const layerRef = useRef(null)
  const nav = useNavigate()

  useEffect(() => {
    const map = L.map(el.current, { zoomControl: true }).setView([5.808, -2.4335], 12)
    mapRef.current = map
    L.tileLayer(SATELLITE, {
      maxZoom: 19, maxNativeZoom: 17,
      attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
    }).addTo(map)
    addReferenceLayers(map)
    return () => { map.remove(); mapRef.current = null; layerRef.current = null }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (layerRef.current) { map.removeLayer(layerRef.current); layerRef.current = null }
    if (!farms?.length) return

    const group = L.featureGroup()
    farms.forEach((f) => {
      const color = colorOf(f)
      const layer = L.geoJSON(f.geometry, {
        style: { color, weight: 2.5, fillColor: color, fillOpacity: 0.28 },
      }).addTo(group)
      layer.bindTooltip(
        `<b>${f.name}</b><br/>${f.code} · ${LABELS[levelKey(f)]}`,
        { sticky: true, className: 'map-label' },
      )
      layer.on('click', () => nav(`/farms/${f.id}`))
    })
    group.addTo(map)
    map.fitBounds(group.getBounds(), { padding: [40, 40], maxZoom: 16 })
    layerRef.current = group
    // nav is stable across renders; re-drawing on farms change only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [farms])

  const counts = farms.reduce((acc, f) => {
    const key = levelKey(f)
    acc[key] = (acc[key] || 0) + 1
    return acc
  }, {})

  return (
    <div className="map-wrap risk-map" style={{ height }}>
      <div ref={el} className="map" />
      <div className="map-legend">
        {Object.entries(LABELS).map(([key, label]) => (
          <span key={key}>
            <i style={{ background: key === 'none' ? UNSCREENED : COLORS[key] }} />
            {label} <b>{counts[key] || 0}</b>
          </span>
        ))}
      </div>
      {!farms?.length && <div className="map-empty small muted">No farms match the current filter.</div>}
    </div>
  )
}
