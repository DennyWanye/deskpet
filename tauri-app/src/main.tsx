// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { createRoot } from 'react-dom/client'
import './index.css'
import { BACKEND_PORT } from './backendPort'

// WI-T1.7 last-mile: 全局 metric emit sink。
// ArtifactCard 按钮点击会调 window.__deskpet_metrics_emit；这里 wire 到
// backend POST /metrics/event，最终落地 <user_data>/metrics.jsonl。
// 无 backend 时静默丢（dev/test 友好），不破 UI。
declare global {
  interface Window {
    __deskpet_metrics_emit?: (event: string, payload: Record<string, unknown>) => void;
  }
}
window.__deskpet_metrics_emit = (event: string, payload: Record<string, unknown>) => {
  try {
    void fetch(`http://127.0.0.1:${BACKEND_PORT}/metrics/event`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event, detail: payload }),
      // fire-and-forget — 不阻 UI
      keepalive: true,
    }).catch(() => { /* silent — backend down or offline */ });
  } catch {
    // Safari old / restricted env — never break UI
  }
};

// P4-S23 — multi-window routing. The main pet shell lives at the
// default URL; the secondary "code-panel" Tauri window opens with
// `index.html#/code-panel` and we render a different React tree
// there. We keep ONE bundle for both windows (saves ~80 KB of
// duplicate ship cost) and just pick the root component by hash.
const isCodePanel = window.location.hash.startsWith('#/code-panel');
// 2026-05-19: slim message panel is its own transparent docked window.
const isMessagePanel = window.location.hash.startsWith('#/message-panel');

if (isCodePanel) {
  // Code panel: opaque dark background, normal scrollbars.
  document.body.style.backgroundColor = '#0f1218';
  document.documentElement.style.backgroundColor = '#0f1218';
} else {
  // Pet shell AND message panel stay fully transparent — the panel
  // paints its own dark glass card so its rounded corners / margins
  // show the desktop (and the window captures no dead area).
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
} else if (isMessagePanel) {
  import('./message-panel/MessagePanelRoot')
    .then(({ MessagePanelRoot }) => root.render(<MessagePanelRoot />))
    .catch((e) => {
      console.error('[main] message-panel load failed:', e);
      root.render(
        <div style={{ padding: 20, color: '#f87171', fontFamily: 'sans-serif' }}>
          Failed to load message panel: {String(e)}
        </div>
      );
    });
} else {
  // Pet shell eagerly imports — needs to start fast.
  import('./App').then(({ default: App }) => root.render(<App />));
}
