import { createRoot } from 'react-dom/client'
import './index.css'

// P4-S23 — multi-window routing. The main pet shell lives at the
// default URL; the secondary "code-panel" Tauri window opens with
// `index.html#/code-panel` and we render a different React tree
// there. We keep ONE bundle for both windows (saves ~80 KB of
// duplicate ship cost) and just pick the root component by hash.
const isCodePanel = window.location.hash.startsWith('#/code-panel');

if (isCodePanel) {
  // Code panel: opaque dark background, normal scrollbars.
  document.body.style.backgroundColor = '#0f1218';
  document.documentElement.style.backgroundColor = '#0f1218';
} else {
  // Pet shell stays fully transparent so the alpha channel reaches
  // the Live2D canvas.
  document.body.style.backgroundColor = 'transparent';
  document.documentElement.style.backgroundColor = 'transparent';
}

// StrictMode intentionally disabled: it double-mounts effects in dev,
// which caused duplicate Live2D PixiJS canvases, double WebSocket
// connections, and repeated Silero-VAD model loads. This is a native
// desktop pet with heavy init cost — the dev-time bug detection
// StrictMode offers isn't worth the resource duplication.
const root = createRoot(document.getElementById('root')!);

if (isCodePanel) {
  // Lazy import keeps react-markdown / pretext / virtuoso out of the
  // pet shell bundle until the user actually opens the panel.
  import('./code-panel/CodePanelRoot')
    .then(({ CodePanelRoot }) => root.render(<CodePanelRoot />))
    .catch((e) => {
      console.error('[main] code-panel load failed:', e);
      root.render(
        <div style={{ padding: 20, color: '#f87171', fontFamily: 'sans-serif' }}>
          Failed to load code panel: {String(e)}
        </div>
      );
    });
} else {
  // Pet shell eagerly imports — needs to start fast.
  import('./App').then(({ default: App }) => root.render(<App />));
}
