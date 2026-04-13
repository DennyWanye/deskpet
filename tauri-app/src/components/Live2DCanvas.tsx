import { useEffect, useRef, useState } from "react";

interface Live2DCanvasProps {
  modelPath: string;
  width?: number;
  height?: number;
  onFpsUpdate?: (fps: number) => void;
}

export function Live2DCanvas({
  width = 400,
  height = 500,
  onFpsUpdate,
}: Live2DCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number>(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    let destroyed = false;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      setError("Canvas 2D context not available");
      return;
    }

    // Scale for HiDPI
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);

    // Animation state
    let frameCount = 0;
    let lastFpsTime = performance.now();
    let eyeBlinkTimer = 0;
    let isBlinking = false;
    let breathOffset = 0;
    let bounceOffset = 0;

    function draw(timestamp: number) {
      if (destroyed) return;

      ctx!.clearRect(0, 0, width, height);

      frameCount++;
      const elapsed = timestamp - lastFpsTime;
      if (elapsed >= 1000) {
        onFpsUpdate?.(Math.round((frameCount * 1000) / elapsed));
        frameCount = 0;
        lastFpsTime = timestamp;
      }

      // Idle animation: gentle breathing + bounce
      breathOffset = Math.sin(timestamp / 1500) * 3;
      bounceOffset = Math.sin(timestamp / 800) * 2;

      const cx = width / 2;
      const cy = height / 2 - 30 + breathOffset;

      // --- Draw character ---

      // Body (rounded rectangle)
      ctx!.fillStyle = "rgba(99, 102, 241, 0.85)";
      roundRect(ctx!, cx - 55, cy + 50, 110, 80, 20);

      // Head (circle)
      ctx!.fillStyle = "rgba(99, 102, 241, 0.9)";
      ctx!.beginPath();
      ctx!.arc(cx, cy + bounceOffset, 65, 0, Math.PI * 2);
      ctx!.fill();

      // Ears
      ctx!.fillStyle = "rgba(129, 140, 248, 0.9)";
      // Left ear
      ctx!.beginPath();
      ctx!.ellipse(cx - 50, cy - 50 + bounceOffset, 18, 25, -0.3, 0, Math.PI * 2);
      ctx!.fill();
      // Right ear
      ctx!.beginPath();
      ctx!.ellipse(cx + 50, cy - 50 + bounceOffset, 18, 25, 0.3, 0, Math.PI * 2);
      ctx!.fill();

      // Inner ears
      ctx!.fillStyle = "rgba(196, 181, 253, 0.7)";
      ctx!.beginPath();
      ctx!.ellipse(cx - 50, cy - 48 + bounceOffset, 10, 15, -0.3, 0, Math.PI * 2);
      ctx!.fill();
      ctx!.beginPath();
      ctx!.ellipse(cx + 50, cy - 48 + bounceOffset, 10, 15, 0.3, 0, Math.PI * 2);
      ctx!.fill();

      // Eyes
      eyeBlinkTimer += 16;
      if (eyeBlinkTimer > 3000 && !isBlinking) {
        isBlinking = true;
        eyeBlinkTimer = 0;
      }
      if (isBlinking && eyeBlinkTimer > 150) {
        isBlinking = false;
        eyeBlinkTimer = 0;
      }

      const eyeY = cy - 8 + bounceOffset;
      const eyeOpenness = isBlinking ? 0.1 : 1;

      // Eye whites
      ctx!.fillStyle = "#ffffff";
      ctx!.beginPath();
      ctx!.ellipse(cx - 22, eyeY, 14, 16 * eyeOpenness, 0, 0, Math.PI * 2);
      ctx!.fill();
      ctx!.beginPath();
      ctx!.ellipse(cx + 22, eyeY, 14, 16 * eyeOpenness, 0, 0, Math.PI * 2);
      ctx!.fill();

      if (!isBlinking) {
        // Pupils (follow a gentle path)
        const pupilX = Math.sin(timestamp / 2000) * 3;
        const pupilY = Math.cos(timestamp / 3000) * 2;

        ctx!.fillStyle = "#1e1b4b";
        ctx!.beginPath();
        ctx!.arc(cx - 22 + pupilX, eyeY + pupilY, 7, 0, Math.PI * 2);
        ctx!.fill();
        ctx!.beginPath();
        ctx!.arc(cx + 22 + pupilX, eyeY + pupilY, 7, 0, Math.PI * 2);
        ctx!.fill();

        // Eye highlights
        ctx!.fillStyle = "#ffffff";
        ctx!.beginPath();
        ctx!.arc(cx - 19 + pupilX, eyeY - 3 + pupilY, 3, 0, Math.PI * 2);
        ctx!.fill();
        ctx!.beginPath();
        ctx!.arc(cx + 25 + pupilX, eyeY - 3 + pupilY, 3, 0, Math.PI * 2);
        ctx!.fill();
      }

      // Blush
      ctx!.fillStyle = "rgba(251, 191, 207, 0.4)";
      ctx!.beginPath();
      ctx!.ellipse(cx - 38, cy + 10 + bounceOffset, 12, 8, 0, 0, Math.PI * 2);
      ctx!.fill();
      ctx!.beginPath();
      ctx!.ellipse(cx + 38, cy + 10 + bounceOffset, 12, 8, 0, 0, Math.PI * 2);
      ctx!.fill();

      // Mouth (small smile)
      ctx!.strokeStyle = "#4338ca";
      ctx!.lineWidth = 2;
      ctx!.beginPath();
      ctx!.arc(cx, cy + 18 + bounceOffset, 10, 0.15, Math.PI - 0.15);
      ctx!.stroke();

      // Tail (wagging)
      const tailWag = Math.sin(timestamp / 300) * 15;
      ctx!.strokeStyle = "rgba(99, 102, 241, 0.8)";
      ctx!.lineWidth = 6;
      ctx!.lineCap = "round";
      ctx!.beginPath();
      ctx!.moveTo(cx + 45, cy + 85);
      ctx!.quadraticCurveTo(cx + 70 + tailWag, cy + 60, cx + 65 + tailWag * 1.5, cy + 35);
      ctx!.stroke();

      rafRef.current = requestAnimationFrame(draw);
    }

    rafRef.current = requestAnimationFrame(draw);

    return () => {
      destroyed = true;
      cancelAnimationFrame(rafRef.current);
    };
  }, [width, height, onFpsUpdate]);

  if (error) {
    return <div style={{ color: "red", padding: 20 }}>Canvas Error: {error}</div>;
  }

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

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
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
