import L from 'leaflet'

const LABELS = 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'
const ROADS = 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}'

/**
 * Esri's place-label + roads reference overlays on top of the satellite imagery -- shared by
 * every map in the app. Both on by default, with a small top-right layer control to switch
 * either off. Same zoom range as the imagery so they never show blank tiles past native zoom 17.
 */
export function addReferenceLayers(map) {
  const opts = { maxZoom: 19, maxNativeZoom: 17 }
  const labels = L.tileLayer(LABELS, opts).addTo(map)
  const roads = L.tileLayer(ROADS, { ...opts, opacity: 0.6 }).addTo(map)
  L.control.layers(null, { 'Labels': labels, 'Roads': roads }, { position: 'topright', collapsed: true }).addTo(map)
}
