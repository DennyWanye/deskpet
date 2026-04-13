import { useEffect, useRef, useState } from "react";

interface Live2DCanvasProps {
  modelPath: string;
  onFpsUpdate?: (fps: number) => void;
}

/**
 * Live2D character rendered via PixiJS WebGL → <img> tag.
 * Falls back to Canvas2D animated cat if Live2D fails.
 *
 * WebView2 transparent windows don't composite <canvas>/<WebGL>.
 * We render offscreen and display each frame via <img> (HTML = composites OK).
 */
export function Live2DCanvas({ modelPath, onFpsUpdate }: Live2DCanvasProps) {
  const imgRef = useRef<HTMLImageElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: window.innerWidth, h: window.innerHeight });
  // Use ref for mode to avoid re-render killing the render loop
  const modeRef = useRef<"loading" | "live2d" | "canvas2d">("loading");
  const [displayMode, setDisplayMode] = useState<string>("loading");
  const cleanupRef = useRef<(() => void) | null>(null);

  // Track viewport
  useEffect(() => {
    const onResize = () => setSize({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("resize", onResize);
    console.warn("[Pet] viewport:", window.innerWidth, "x", window.innerHeight, "dpr:", window.devicePixelRatio);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Main init — runs once
  useEffect(() => {
    if (modeRef.current !== "loading") return;

    let destroyed = false;
    let pixiApp: any = null;
    let rafId = 0;

    async function init() {
      try {
        console.warn("[Live2D] starting PixiJS...");
        const PIXI = await import("pixi.js");
        (window as any).PIXI = PIXI;

        if (destroyed) return;

        const dpr = window.devicePixelRatio || 1;
        const renderW = Math.round(size.w * dpr);
        const renderH = Math.round(size.h * dpr);

        pixiApp = new PIXI.Application({
          width: renderW,
          height: renderH,
          backgroundAlpha: 0,
          antialias: true,
          preserveDrawingBuffer: true,
          resolution: 1,
        });

        if (destroyed) { pixiApp.destroy(true); return; }

        // Append canvas to DOM (hidden) — WebGL needs DOM presence to render
        const pixiCanvas = pixiApp.view as HTMLCanvasElement;
        pixiCanvas.style.cssText = "position:fixed;top:-9999px;left:-9999px;pointer-events:none;";
        document.body.appendChild(pixiCanvas);

        console.warn("[Live2D] PixiJS created, loading cubism4...");
        const { Live2DModel } = await import("pixi-live2d-display/cubism4");
        if (destroyed) return;

        console.warn("[Live2D] loading model:", modelPath);
        const model = await Promise.race([
          Live2DModel.from(modelPath),
          new Promise((_, rej) => setTimeout(() => rej(new Error("timeout 15s")), 15000)),
        ]) as any;

        if (destroyed) return;
        console.warn("[Live2D] model loaded:", model.width, "x", model.height);

        model.autoInteract = false;

        // Scale model to fit
        const scaleX = (renderW * 0.85) / model.width;
        const scaleY = (renderH * 0.7) / model.height;
        const scale = Math.min(scaleX, scaleY);
        model.scale.set(scale);
        model.x = (renderW - model.width * scale) / 2;
        model.y = (renderH - model.height * scale) * 0.25;

        pixiApp.stage.addChild(model);

        modeRef.current = "live2d";
        setDisplayMode("live2d");
        console.warn("[Live2D] render loop starting");

        // Render loop — cap at ~30fps for performance
        // toBlob + createObjectURL is faster than toDataURL (no base64 encoding)
        const TARGET_FPS = 30;
        const FRAME_INTERVAL = 1000 / TARGET_FPS;
        let frameCount = 0;
        let lastFpsTime = performance.now();
        let lastFrameTime = 0;
        let pendingBlob = false;
        let currentBlobUrl: string | null = null;

        function renderLoop(timestamp: number) {
          if (destroyed) return;

          // Throttle frame extraction to TARGET_FPS
          const delta = timestamp - lastFrameTime;
          if (delta >= FRAME_INTERVAL && !pendingBlob) {
            lastFrameTime = timestamp - (delta % FRAME_INTERVAL);
            frameCount++;

            const now = performance.now();
            if (now - lastFpsTime >= 1000) {
              onFpsUpdate?.(Math.round((frameCount * 1000) / (now - lastFpsTime)));
              frameCount = 0;
              lastFpsTime = now;
            }

            // Extract frame via toBlob (async, faster than toDataURL)
            if (imgRef.current && pixiApp?.view) {
              pendingBlob = true;
              try {
                (pixiApp.view as HTMLCanvasElement).toBlob(
                  (blob: Blob | null) => {
                    pendingBlob = false;
                    if (destroyed || !blob || !imgRef.current) return;
                    // Revoke previous blob URL to prevent memory leak
                    if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
                    currentBlobUrl = URL.createObjectURL(blob);
                    imgRef.current.src = currentBlobUrl;
                  },
                  "image/webp",
                  0.8,
                );
              } catch {
                pendingBlob = false;
              }
            }
          }

          rafId = requestAnimationFrame(renderLoop);
        }
        rafId = requestAnimationFrame(renderLoop);

      } catch (err) {
        console.warn("[Live2D] failed:", err);
        try {
          const pixiCanvas = pixiApp?.view as HTMLCanvasElement;
          if (pixiCanvas?.parentNode) pixiCanvas.parentNode.removeChild(pixiCanvas);
          pixiApp?.destroy(true);
        } catch { /* ignore */ }
        pixiApp = null;

        if (!destroyed) {
          modeRef.current = "canvas2d";
          setDisplayMode("canvas2d");
          startCanvas2D();
        }
      }
    }

    // Canvas2D fallback
    function startCanvas2D() {
      console.warn("[Canvas2D] starting fallback");
      const width = size.w;
      const height = size.h;
      const canvas = document.createElement("canvas");
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      let frameCount = 0;
      let lastFpsTime = performance.now();
      let eyeBlinkTimer = 0;
      let isBlinking = false;
      const cs = Math.min(width / 300, height / 450, 1);

      function draw(ts: number) {
        if (destroyed) return;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);

        frameCount++;
        const now = performance.now();
        if (now - lastFpsTime >= 1000) {
          onFpsUpdate?.(Math.round((frameCount * 1000) / (now - lastFpsTime)));
          frameCount = 0;
          lastFpsTime = now;
        }

        ctx.save();
        ctx.translate(width / 2, height * 0.38);
        ctx.scale(cs, cs);
        const by = Math.sin(ts / 1500) * 3, bo = Math.sin(ts / 800) * 2;

        // Shadow
        ctx.fillStyle = "rgba(0,0,0,0.15)";
        ctx.beginPath(); ctx.ellipse(0, 140 + by, 55, 10, 0, 0, Math.PI * 2); ctx.fill();
        // Tail
        const tw = Math.sin(ts / 300) * 15;
        ctx.strokeStyle = "rgba(99,102,241,0.8)"; ctx.lineWidth = 8; ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(45, 85 + by); ctx.quadraticCurveTo(75 + tw, 55 + by, 70 + tw * 1.5, 25 + by); ctx.stroke();
        // Body
        ctx.fillStyle = "rgba(99,102,241,0.9)"; roundRect(ctx, -55, 50 + by, 110, 80, 22);
        ctx.fillStyle = "rgba(129,140,248,0.3)"; roundRect(ctx, -40, 55 + by, 80, 25, 12);
        // Paws
        ctx.fillStyle = "rgba(129,140,248,0.9)";
        ctx.beginPath(); ctx.ellipse(-35, 130 + by, 18, 10, -0.1, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.ellipse(35, 130 + by, 18, 10, 0.1, 0, Math.PI * 2); ctx.fill();
        // Head
        ctx.fillStyle = "rgba(99,102,241,0.95)";
        ctx.beginPath(); ctx.arc(0, bo, 65, 0, Math.PI * 2); ctx.fill();
        // Ears
        ctx.fillStyle = "rgba(99,102,241,0.95)";
        ctx.beginPath(); ctx.moveTo(-55, -20 + bo); ctx.lineTo(-70, -70 + bo); ctx.lineTo(-25, -45 + bo); ctx.closePath(); ctx.fill();
        ctx.beginPath(); ctx.moveTo(55, -20 + bo); ctx.lineTo(70, -70 + bo); ctx.lineTo(25, -45 + bo); ctx.closePath(); ctx.fill();
        ctx.fillStyle = "rgba(196,181,253,0.7)";
        ctx.beginPath(); ctx.moveTo(-52, -25 + bo); ctx.lineTo(-63, -60 + bo); ctx.lineTo(-32, -42 + bo); ctx.closePath(); ctx.fill();
        ctx.beginPath(); ctx.moveTo(52, -25 + bo); ctx.lineTo(63, -60 + bo); ctx.lineTo(32, -42 + bo); ctx.closePath(); ctx.fill();
        // Eyes
        eyeBlinkTimer += 16;
        if (eyeBlinkTimer > 3000 && !isBlinking) { isBlinking = true; eyeBlinkTimer = 0; }
        if (isBlinking && eyeBlinkTimer > 150) { isBlinking = false; eyeBlinkTimer = 0; }
        const ey = -8 + bo, eo = isBlinking ? 0.1 : 1;
        ctx.fillStyle = "#fff";
        ctx.beginPath(); ctx.ellipse(-24, ey, 16, 18 * eo, 0, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.ellipse(24, ey, 16, 18 * eo, 0, 0, Math.PI * 2); ctx.fill();
        if (!isBlinking) {
          const px = Math.sin(ts / 2000) * 4, py = Math.cos(ts / 3000) * 2;
          ctx.fillStyle = "#1e1b4b";
          ctx.beginPath(); ctx.arc(-24 + px, ey + py, 8, 0, Math.PI * 2); ctx.fill();
          ctx.beginPath(); ctx.arc(24 + px, ey + py, 8, 0, Math.PI * 2); ctx.fill();
          ctx.fillStyle = "rgba(255,255,255,0.9)";
          ctx.beginPath(); ctx.arc(-20 + px, ey - 4 + py, 4, 0, Math.PI * 2); ctx.fill();
          ctx.beginPath(); ctx.arc(28 + px, ey - 4 + py, 3, 0, Math.PI * 2); ctx.fill();
        }
        // Blush
        ctx.fillStyle = "rgba(251,191,207,0.45)";
        ctx.beginPath(); ctx.ellipse(-42, 12 + bo, 14, 8, 0, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.ellipse(42, 12 + bo, 14, 8, 0, 0, Math.PI * 2); ctx.fill();
        // Nose + Mouth
        ctx.fillStyle = "rgba(196,181,253,0.8)";
        ctx.beginPath(); ctx.moveTo(0, 8 + bo); ctx.lineTo(-5, 14 + bo); ctx.lineTo(5, 14 + bo); ctx.closePath(); ctx.fill();
        ctx.strokeStyle = "#4338ca"; ctx.lineWidth = 2; ctx.lineCap = "round";
        ctx.beginPath(); ctx.arc(-8, 16 + bo, 8, -0.3, Math.PI * 0.7); ctx.stroke();
        ctx.beginPath(); ctx.arc(8, 16 + bo, 8, Math.PI * 0.3, Math.PI + 0.3); ctx.stroke();
        // Whiskers
        ctx.strokeStyle = "rgba(200,200,220,0.5)"; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(-30, 10 + bo); ctx.lineTo(-65, 5 + bo); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(-30, 16 + bo); ctx.lineTo(-65, 18 + bo); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(30, 10 + bo); ctx.lineTo(65, 5 + bo); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(30, 16 + bo); ctx.lineTo(65, 18 + bo); ctx.stroke();
        ctx.restore();

        // Use toBlob + createObjectURL (faster than toDataURL)
        canvas.toBlob(
          (blob) => {
            if (!blob || !imgRef.current || destroyed) return;
            if (c2dBlobUrl) URL.revokeObjectURL(c2dBlobUrl);
            c2dBlobUrl = URL.createObjectURL(blob);
            imgRef.current.src = c2dBlobUrl;
          },
          "image/webp",
          0.8,
        );
        rafId = requestAnimationFrame(draw);
      }
      let c2dBlobUrl: string | null = null;
      rafId = requestAnimationFrame(draw);
    }

    init();

    cleanupRef.current = () => {
      destroyed = true;
      cancelAnimationFrame(rafId);
      try {
        const pixiCanvas = pixiApp?.view as HTMLCanvasElement;
        if (pixiCanvas?.parentNode) pixiCanvas.parentNode.removeChild(pixiCanvas);
        pixiApp?.destroy(true);
      } catch { /* ignore */ }
    };

    return () => {
      cleanupRef.current?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Run once only

  return (
    <>
      <div ref={containerRef} style={{ display: "none" }} />
      <img
        ref={imgRef}
        alt=""
        style={{
          width: `${size.w}px`,
          height: `${size.h}px`,
          position: "absolute",
          top: 0,
          left: 0,
          pointerEvents: "none",
        }}
      />
    </>
  );
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, r: number,
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
  ctx.fill();
}
