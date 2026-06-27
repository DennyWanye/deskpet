// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Tier-1「努力工作」气泡。active=true 时显示在角色头顶：💭 + 文案 +
 * 三点跳动。覆盖层 pointer-events:none。位置参考 PetCelebrationBubble
 * (bottom 区 + right:28)，略高避免与庆祝气泡重叠。
 */
export function PetWorkingBubble({ active, label = "努力工作中" }: { active: boolean; label?: string }) {
  if (!active) return null;
  return (
    <div
      style={{
        position: "fixed",
        bottom: 250,
        right: 28,
        zIndex: 35,
        pointerEvents: "none",
        background: "rgba(30,27,46,0.82)",
        backdropFilter: "blur(8px)",
        color: "#e9e4ff",
        borderRadius: 14,
        padding: "7px 12px",
        fontSize: 12,
        display: "flex",
        alignItems: "center",
        gap: 6,
        boxShadow: "0 4px 16px rgba(0,0,0,0.3)",
      }}
    >
      <span style={{ fontSize: 14 }}>💭</span>
      <span>{label}</span>
      <span className="pet-dots"><i></i><i></i><i></i></span>
      <style>{`
        .pet-dots i {
          display: inline-block; width: 4px; height: 4px; margin: 0 1px;
          background: #c4b5fd; border-radius: 50%;
          animation: pet-dot 1s infinite ease-in-out;
        }
        .pet-dots i:nth-child(2) { animation-delay: 0.18s; }
        .pet-dots i:nth-child(3) { animation-delay: 0.36s; }
        @keyframes pet-dot { 0%,80%,100% { opacity:0.3; transform:translateY(0) } 40% { opacity:1; transform:translateY(-3px) } }
      `}</style>
    </div>
  );
}
