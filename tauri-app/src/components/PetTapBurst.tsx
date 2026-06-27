// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { useState } from "react";

interface Burst { id: number; x: number; y: number }

/**
 * Tier-1 点击星星特效。父组件每次点击调 spawn(x, y)，在该坐标爆 5 颗
 * 星星，~650ms 上飘+淡出后自动清除。覆盖层 pointer-events:none 不挡交互。
 */
export function usePetTapBurst() {
  const [bursts, setBursts] = useState<Burst[]>([]);
  const spawn = (x: number, y: number) => {
    const id = performance.now() + Math.random();
    setBursts((b) => [...b, { id, x, y }]);
    window.setTimeout(() => setBursts((b) => b.filter((it) => it.id !== id)), 650);
  };
  return { bursts, spawn };
}

export function PetTapBurst({ bursts }: { bursts: { id: number; x: number; y: number }[] }) {
  return (
    <>
      {bursts.map((b) =>
        Array.from({ length: 5 }).map((_, i) => {
          const ang = (i / 5) * Math.PI * 2;
          const dx = Math.cos(ang) * 26;
          const dy = Math.sin(ang) * 26 - 14;
          return (
            <span
              key={`${b.id}-${i}`}
              style={{
                position: "fixed",
                left: b.x,
                top: b.y,
                pointerEvents: "none",
                zIndex: 40,
                fontSize: 16,
                ["--dx" as string]: `${dx}px`,
                ["--dy" as string]: `${dy}px`,
                animation: "pet-star 600ms ease-out forwards",
              }}
            >
              ✨
            </span>
          );
        }),
      )}
      <style>{`
        @keyframes pet-star {
          0%   { transform: translate(0,0) scale(0.4); opacity: 0; }
          25%  { opacity: 1; }
          100% { transform: translate(var(--dx), var(--dy)) scale(1.1); opacity: 0; }
        }
      `}</style>
    </>
  );
}
