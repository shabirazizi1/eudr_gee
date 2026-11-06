import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import L from 'leaflet'
import { addReferenceLayers } from './mapLayers.js'

const SATELLITE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
const STYLE = { color: '#ffe034', weight: 3, fillColor: '#ffe034', fillOpacity: 0.12 }

/** Shows every contributing farm's boundary on one map. Clicking a farm opens its result page. */
export default function BatchMap({ farms, height = 420 }) {
  const el = useRef(null)
  const mapRef = useRef(null)
  const nav = useNavigate()

  useEffect(() => {
    const map = L.map(el.current, { zoomControl: true }).setView([5.808, -2.4335], 12)
    mapRef.current = map
    L.tileLayer(SATELLITE, {
      maxZoom: 19, maxNativeZoom: 17,
      attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
    }).addTo(map)
    addReferenceLayers(map)
    return () => { map.remove(); mapRef.current = null }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !farms?.length) return
    const group = L.featureGroup()
    farms.forEach((f) => {
      const layer = L.geoJSON(f.geometry, { style: STYLE }).addTo(group)
      layer.bindTooltip(f.name, { permanent: true, direction: 'top', offset: [0, -6], className: 'map-label' })
      layer.on('click', () => nav(`/farms/${f.id}`))
    })
    group.addTo(map)
    map.fitBounds(group.getBounds(), { padding: [40, 40], maxZoom: 17 })
    return () => { map.removeLayer(group) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [farms])

  return (
    <div className="map-wrap" style={{ height }}>
      <div ref={el} className="map" />
    </div>
  )
}
