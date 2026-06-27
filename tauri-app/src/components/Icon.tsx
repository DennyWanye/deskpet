// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Icon — 统一的线性图标组件。
 *
 * 桌宠全 UI 的图标单一来源。取代分散在各处的 emoji（🗂⚙🏪…）——
 * emoji 跨平台渲染不一致、像素感重、缺乏高级感。这里用 24×24 viewBox
 * 的描边图标，`stroke="currentColor"` 跟随父级 color，线宽 1.7、圆角
 * 端点，呈现克制、精致的现代线性风格。
 */
import type { CSSProperties, ReactElement } from "react";

export type IconName =
  | "archive"
  | "compass"
  | "settings"
  | "store"
  | "bug"
  | "terminal"
  | "power"
  | "mic"
  | "mic-off"
  | "send"
  | "close"
  | "chevron-right"
  | "chevron-left"
  | "stop"
  | "hand"
  | "sparkle"
  | "search"
  | "plus"
  | "trash"
  | "check"
  | "alert"
  | "refresh"
  | "folder"
  | "cpu"
  | "message"
  | "expand"
  | "pin"
  | "edit"
  | "grid"
  | "user";

type Props = {
  name: IconName;
  size?: number;
  strokeWidth?: number;
  style?: CSSProperties;
  className?: string;
};

// 每个图标只存内部 path/shape 集合，统一 viewBox 24×24。
const PATHS: Record<IconName, ReactElement> = {
  archive: (
    <>
      <rect x="3" y="4" width="18" height="4" rx="1.2" />
      <path d="M5 8v10.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V8" />
      <path d="M10 12h4" />
    </>
  ),
  compass: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m15.5 8.5-2 5.5-5 2 2-5.5 5-2Z" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M19.4 13.5a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.56V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.56-1.1 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34H9a1.7 1.7 0 0 0 1-1.56V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9a1.7 1.7 0 0 0 1.56 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1.5Z" />
    </>
  ),
  store: (
    <>
      <path d="M4 9.5 5.2 4.5A1 1 0 0 1 6.17 3.8h11.66a1 1 0 0 1 .97.7L20 9.5" />
      <path d="M4 9.5h16v1a3 3 0 0 1-6 0 3 3 0 0 1-6 0 3 3 0 0 1-4 0Z" />
      <path d="M5.5 13v6.2a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V13" />
      <path d="M10 20.2V15.5h4v4.7" />
    </>
  ),
  bug: (
    <>
      <rect x="8" y="7" width="8" height="11" rx="4" />
      <path d="M12 4.5V7M9 6 7.5 4.5M15 6l1.5-1.5M8 11H4.5M16 11h3.5M8 15H4.5M16 15h3.5M8.5 8 6 6M15.5 8 18 6M8.5 18 6 20.5M15.5 18 18 20.5" />
    </>
  ),
  terminal: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2.2" />
      <path d="m7.5 9.5 2.5 2.5-2.5 2.5M12.5 15h4" />
    </>
  ),
  power: (
    <>
      <path d="M12 3.5v8" />
      <path d="M7.5 6.8a7 7 0 1 0 9 0" />
    </>
  ),
  mic: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M6 11a6 6 0 0 0 12 0M12 17v4M9 21h6" />
    </>
  ),
  "mic-off": (
    <>
      <path d="M15 5a3 3 0 0 0-6 0v5M9 12.5A3 3 0 0 0 15 12" />
      <path d="M6 11a6 6 0 0 0 9.5 4.9M18 11a6 6 0 0 1-.3 1.9M12 17v4M9 21h6M4 4l16 16" />
    </>
  ),
  send: (
    <>
      <path d="M21 3 10.5 13.5M21 3l-6.5 18-4-8.5L2 8.5 21 3Z" />
    </>
  ),
  close: <path d="M6 6 18 18M18 6 6 18" />,
  "chevron-right": <path d="m9 5 7 7-7 7" />,
  "chevron-left": <path d="m15 5-7 7 7 7" />,
  stop: <rect x="6" y="6" width="12" height="12" rx="2.4" />,
  hand: (
    <path d="M7 11V6.5a1.5 1.5 0 0 1 3 0V10m0-3.5V5a1.5 1.5 0 0 1 3 0v5m0-4.5a1.5 1.5 0 0 1 3 0V11m0-2.5a1.5 1.5 0 0 1 3 0V14a7 7 0 0 1-7 7h-1.5a6 6 0 0 1-4.4-2L4 15.5a1.6 1.6 0 0 1 2.6-1.8L8 15" />
  ),
  sparkle: (
    <path d="M12 3.5 13.8 9 19.5 10.8 13.8 12.6 12 18l-1.8-5.4L4.5 10.8 10.2 9 12 3.5ZM19 4l.8 2 .7-2 .5-.7M5 17l.6 1.7L7 19l-1.4.6L5 21l-.6-1.5L3 19l1.4-.6Z" />
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-4-4" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  trash: (
    <path d="M4 7h16M10 4h4M9.5 7l.7 12.5a1.5 1.5 0 0 0 1.5 1.4h.6a1.5 1.5 0 0 0 1.5-1.4L15.5 7M10 11v6M14 11v6" />
  ),
  check: <path d="m5 12.5 5 5 9-11" />,
  alert: (
    <>
      <path d="M12 3.5 22 20H2L12 3.5Z" />
      <path d="M12 9.5v5M12 17.4v.1" />
    </>
  ),
  refresh: (
    <path d="M3.5 12a8.5 8.5 0 0 1 14.5-6M20.5 12a8.5 8.5 0 0 1-14.5 6M17 3v3.5h-3.5M7 21v-3.5h3.5" />
  ),
  folder: (
    <path d="M3.5 7.5a2 2 0 0 1 2-2h3.7a2 2 0 0 1 1.4.6l1.3 1.3a2 2 0 0 0 1.4.6h4.8a2 2 0 0 1 2 2v8.4a2 2 0 0 1-2 2H5.5a2 2 0 0 1-2-2V7.5Z" />
  ),
  cpu: (
    <>
      <rect x="7" y="7" width="10" height="10" rx="2" />
      <path d="M10 2.5V5M14 2.5V5M10 19v2.5M14 19v2.5M2.5 10H5M2.5 14H5M19 10h2.5M19 14h2.5" />
    </>
  ),
  message: (
    <path d="M4 5.5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H9l-4 4v-4H6a2 2 0 0 1-2-2v-8Z" />
  ),
  expand: (
    <path d="M9 4H5a1 1 0 0 0-1 1v4M15 4h4a1 1 0 0 1 1 1v4M9 20H5a1 1 0 0 1-1-1v-4M15 20h4a1 1 0 0 0 1-1v-4" />
  ),
  pin: (
    <path d="M9 4h6l-1 6 3.5 3.5H5.5L9 10 8 4ZM12 13.5V21" />
  ),
  edit: (
    <path d="M4 20h4L18.5 9.5a2 2 0 0 0-2.83-2.83L5 17.5V20ZM14 8l2 2" />
  ),
  grid: (
    <>
      <rect x="4" y="4" width="7" height="7" rx="1.6" />
      <rect x="13" y="4" width="7" height="7" rx="1.6" />
      <rect x="4" y="13" width="7" height="7" rx="1.6" />
      <rect x="13" y="13" width="7" height="7" rx="1.6" />
    </>
  ),
  user: (
    <>
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </>
  ),
};

export function Icon({ name, size = 16, strokeWidth = 1.7, style, className }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ display: "block", flexShrink: 0, ...style }}
      className={className}
    >
      {PATHS[name]}
    </svg>
  );
}

export default Icon;
