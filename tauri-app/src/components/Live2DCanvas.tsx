import { useEffect, useRef, useState } from "react";
import * as PIXI from "pixi.js";

interface Live2DCanvasProps {
  modelPath: string;
  width?: number;
  height?: number;
  onFpsUpdate?: (fps: number) => void;
}

export function Live2DCanvas({
  modelPath,
  width = 400,
  height = 600,
  onFpsUpdate,
}: Live2DCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const appRef = useRef<PIXI.Application | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!canvasRef.current) return;
    let destroyed = false;

    async function init() {
      try {
        const app = new PIXI.Application();
        await app.init({
          canvas: canvasRef.current!,
          width,
          height,
          backgroundAlpha: 0,
          antialias: true,
          resolution: window.devicePixelRatio || 1,
          autoDensity: true,
        });

        if (destroyed) {
          app.destroy();
          return;
        }
        appRef.current = app;

        // Try loading Live2D model
        let modelLoaded = false;
        try {
          // pixi-live2d-display requires global PIXI
          (window as unknown as Record<string, unknown>).PIXI = PIXI;

          const { Live2DModel } = await import("pixi-live2d-display");
          const model = await Live2DModel.from(modelPath, {
            autoInteract: false,
          });
          const scale =
            Math.min(width / model.width, height / model.height) * 0.8;
          model.scale.set(scale);
          model.x = (width - model.width * scale) / 2;
          model.y = (height - model.height * scale) / 2;
          app.stage.addChild(model as unknown as PIXI.Container);
          modelLoaded = true;
        } catch (modelErr) {
          console.warn("Live2D model not loaded:", modelErr);
        }

        // Show placeholder if model didn't load
        if (!modelLoaded) {
          const placeholder = new PIXI.Graphics();
          // Face circle
          placeholder.circle(width / 2, height / 2 - 50, 80);
          placeholder.fill({ color: 0x6366f1, alpha: 0.7 });

          // Eyes
          const eyes = new PIXI.Graphics();
          eyes.circle(width / 2 - 25, height / 2 - 65, 10);
          eyes.circle(width / 2 + 25, height / 2 - 65, 10);
          eyes.fill({ color: 0xffffff });

          // Smile
          const smile = new PIXI.Graphics();
          smile.arc(width / 2, height / 2 - 40, 30, 0.2, Math.PI - 0.2);
          smile.stroke({ color: 0xffffff, width: 3 });

          app.stage.addChild(placeholder);
          app.stage.addChild(eyes);
          app.stage.addChild(smile);
        }

        // FPS reporting
        if (onFpsUpdate) {
          app.ticker.add(() => {
            onFpsUpdate(Math.round(app.ticker.FPS));
          });
        }
      } catch (err) {
        if (!destroyed) setError(String(err));
      }
    }

    init();
    return () => {
      destroyed = true;
      if (appRef.current) {
        appRef.current.destroy(true);
        appRef.current = null;
      }
    };
  }, [modelPath, width, height, onFpsUpdate]);

  if (error)
    return (
      <div style={{ color: "red", padding: 20 }}>Live2D Error: {error}</div>
    );

  return (
    <canvas
      ref={canvasRef}
      style={{
        width: `${width}px`,
        height: `${height}px`,
        position: "absolute",
        top: 0,
        left: 0,
      }}
    />
  );
}
