import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

document.body.style.backgroundColor = "transparent";
document.documentElement.style.backgroundColor = "transparent";

// StrictMode intentionally disabled: it double-mounts effects in dev, which
// caused duplicate Live2D PixiJS canvases (visible as overlapping characters),
// double WebSocket connections, and repeated Silero-VAD model loads.
// This is a native desktop pet with heavy init cost — the dev-time bug
// detection StrictMode offers isn't worth the resource duplication.
createRoot(document.getElementById('root')!).render(<App />)
