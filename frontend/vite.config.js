import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import basicSsl from '@vitejs/plugin-basic-ssl'

// `npm run dev:phone` (mode "phone") serves over HTTPS with a self-signed cert so a phone on the
// same Wi-Fi can open the app -- GPS (navigator.geolocation) needs a secure context off localhost.
export default defineConfig(({ mode }) => ({
  plugins: [react(), ...(mode === 'phone' ? [basicSsl()] : [])],
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:8000' },
  },
}))
