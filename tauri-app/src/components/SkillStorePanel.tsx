/**
 * P4-S20 Stage C — SkillStorePanel (UI-revamp)
 *
 * Three tabs: Installed / Marketplace / Add by URL.
 * 全部样式走 design tokens；列表卡片化 + 错误分级 (info / warn / error)。
 *
 * 后端 IPC：
 *   skill_marketplace_list / skill_marketplace_list_response
 *   skill_list_installed / skill_list_installed_response
 *   skill_install_from_url / skill_install_pending
 *   skill_install_confirm / skill_install_confirm_response
 *   skill_uninstall / skill_uninstall_response
 *
 * 规格：openspec/specs/skill-marketplace/spec.md
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";

import type { ControlChannel } from "../ws/ControlChannel";
import type {
  MarketplaceSkill,
  SkillMeta,
  PermissionCategory,
} from "../types/skillPlatform";
import {
  badgeStyle,
  bannerStyle,
  buttonStyle,
  cardStyle,
  inputStyle,
  surfaceLight,
  tabStyle,
} from "../theme/components";
import { tokens } from "../theme/tokens";

type Tab = "installed" | "marketplace" | "add-url";

type AlertLevel = "info" | "warning" | "error";
interface AlertState {
  level: AlertLevel;
  message: string;
}

interface Props {
  open: boolean;
  channel: ControlChannel | null;
  onClose: () => void;
}

interface StagedSkill {
  staging_id: string;
  name: string;
  manifest: Record<string, unknown>;
  permission_categories: PermissionCategory[];
}

const SENSITIVE_CATS: PermissionCategory[] = [
  "shell",
  "skill_install",
  "read_file_sensitive",
];

const TAB_LABEL: Record<Tab, string> = {
  installed: "已安装",
  marketplace: "市场",
  "add-url": "通过 URL 安装",
};

export const SkillStorePanel: React.FC<Props> = ({ open, channel, onClose }) => {
  const [tab, setTab] = useState<Tab>("installed");
  const [installed, setInstalled] = useState<SkillMeta[]>([]);
  const [marketplace, setMarketplace] = useState<MarketplaceSkill[]>([]);
  const [alert, setAlert] = useState<AlertState | null>(null);
  const [urlInput, setUrlInput] = useState("");
  const [staged, setStaged] = useState<StagedSkill | null>(null);
  const [loading, setLoading] = useState(false);

  // 监听 backend response
  useEffect(() => {
    if (!open || !channel) return undefined;
    const off = channel.onMessage((msg) => {
      const t = (msg as { type?: string }).type;
      const payload = (msg as { payload?: Record<string, unknown> }).payload || {};
      if (t === "skill_marketplace_list_response") {
        setMarketplace((payload.skills as MarketplaceSkill[]) || []);
        if (payload.error) {
          const raw = String(payload.error);
          // 404 = registry 还没发布 — info 级，不要红
          if (raw.includes("404")) {
            setAlert({
              level: "info",
              message:
                "官方注册表暂未发布。请使用「通过 URL 安装」直接安装 GitHub 上的技能。",
            });
          } else {
            setAlert({
              level: "warning",
              message: `注册表加载失败：${raw}`,
            });
          }
        }
        setLoading(false);
      } else if (t === "skill_list_installed_response") {
        setInstalled((payload.skills as SkillMeta[]) || []);
        if (payload.error) {
          setAlert({ level: "warning", message: String(payload.error) });
        }
        setLoading(false);
      } else if (t === "skill_install_pending") {
        setLoading(false);
        if (payload.ok) {
          setStaged({
            staging_id: payload.staging_id as string,
            name: payload.name as string,
            manifest: payload.manifest as Record<string, unknown>,
            permission_categories:
              (payload.permission_categories as PermissionCategory[]) || [],
          });
        } else {
          setAlert({
            level: "error",
            message: String(payload.error || "安装失败"),
          });
        }
      } else if (t === "skill_install_confirm_response") {
        setLoading(false);
        if (payload.ok) {
          setStaged(null);
          setUrlInput("");
          setAlert({
            level: "info",
            message: `已安装：${payload.name as string}`,
          });
          channel.send({ type: "skill_list_installed" });
          setTab("installed");
        } else {
          setAlert({
            level: "error",
            message: String(payload.error || payload.reason || "安装失败"),
          });
        }
      } else if (t === "skill_uninstall_response") {
        setLoading(false);
        if (payload.ok) {
          channel.send({ type: "skill_list_installed" });
          setAlert({
            level: "info",
            message: `已卸载：${payload.name as string}`,
          });
        } else {
          setAlert({
            level: "error",
            message: String(payload.error || "卸载失败"),
          });
        }
      }
    });
    return () => off();
  }, [open, channel]);

  // tab 切换 / 打开时刷新
  useEffect(() => {
    if (!open || !channel) return;
    setAlert(null);
    setLoading(true);
    if (tab === "installed") {
      channel.send({ type: "skill_list_installed" });
    } else if (tab === "marketplace") {
      channel.send({ type: "skill_marketplace_list" });
    } else {
      setLoading(false);
    }
  }, [tab, open, channel]);

  const installFromUrl = useCallback(
    (url: string) => {
      if (!channel || !url.trim()) return;
      setAlert(null);
      setLoading(true);
      channel.send({
        type: "skill_install_from_url",
        payload: { url: url.trim() },
      });
    },
    [channel]
  );

  const confirmInstall = useCallback(
    (approve: boolean) => {
      if (!channel || !staged) return;
      setLoading(true);
      channel.send({
        type: "skill_install_confirm",
        payload: { staging_id: staged.staging_id, approve },
      });
    },
    [channel, staged]
  );

  const uninstall = useCallback(
    (name: string) => {
      if (!channel) return;
      if (!confirm(`确认卸载 “${name}” 吗？`)) return;
      setLoading(true);
      channel.send({ type: "skill_uninstall", payload: { name } });
    },
    [channel]
  );

  const sensitiveBadges = useMemo(() => {
    if (!staged) return [];
    return staged.permission_categories.filter((c) =>
      SENSITIVE_CATS.includes(c)
    );
  }, [staged]);

  if (!open) return null;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: tokens.color.surface.lightBackdrop,
        backdropFilter: "blur(2px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 9000,
        animation: `bp-fade-in ${tokens.duration.base}ms ${tokens.easing.out}`,
      }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="skill-store-title"
    >
      <div
        style={{
          ...surfaceLight,
          width: 720,
          maxWidth: "94vw",
          maxHeight: "86vh",
          display: "flex",
          flexDirection: "column",
          animation: `bp-pop-in ${tokens.duration.base}ms ${tokens.easing.out}`,
          position: "relative",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            padding: `${tokens.space.lg}px ${tokens.space.lg}px 0`,
            display: "flex",
            alignItems: "flex-start",
            gap: tokens.space.md,
          }}
        >
          <div style={{ flex: 1 }}>
            <div
              style={{
                fontSize: tokens.text.xs.size,
                color: tokens.color.neutral[500],
                fontWeight: tokens.weight.medium,
                marginBottom: 2,
              }}
            >
              SkillStore
            </div>
            <h2
              id="skill-store-title"
              style={{
                margin: 0,
                fontSize: tokens.text.xl.size,
                fontWeight: tokens.weight.semibold,
                color: tokens.color.neutral[900],
              }}
            >
              技能商店
            </h2>
          </div>
          <button
            type="button"
            className="bp-btn-ghost"
            onClick={onClose}
            style={{
              ...buttonStyle("ghost", "sm"),
              width: 32,
              height: 32,
              padding: 0,
              fontSize: tokens.text.xl.size,
              color: tokens.color.neutral[500],
            }}
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        {/* Tabs */}
        <div
          style={{
            padding: `${tokens.space.md}px ${tokens.space.lg}px 0`,
            display: "flex",
            gap: tokens.space.xs,
          }}
        >
          {(Object.keys(TAB_LABEL) as Tab[]).map((t) => {
            const active = tab === t;
            return (
              <button
                key={t}
                type="button"
                className={`bp-tab ${active ? "bp-tab-active" : ""}`}
                onClick={() => setTab(t)}
                style={tabStyle(active)}
              >
                {TAB_LABEL[t]}
              </button>
            );
          })}
        </div>

        {/* Alert */}
        {alert && (
          <div
            style={{
              margin: `${tokens.space.md}px ${tokens.space.lg}px 0`,
            }}
          >
            <div
              style={{
                ...bannerStyle(
                  alert.level === "warning" ? "warning" : alert.level === "error" ? "error" : "info"
                ),
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: tokens.space.sm,
              }}
              role={alert.level === "error" ? "alert" : "status"}
            >
              <span>{alert.message}</span>
              <button
                type="button"
                className="bp-btn-ghost"
                onClick={() => setAlert(null)}
                style={{
                  ...buttonStyle("ghost", "sm"),
                  padding: "0 6px",
                  fontSize: tokens.text.md.size,
                  color: "currentColor",
                  opacity: 0.6,
                }}
                aria-label="关闭提示"
              >
                ✕
              </button>
            </div>
          </div>
        )}

        {/* Body */}
        <div
          style={{
            overflow: "auto",
            flex: 1,
            padding: `${tokens.space.md}px ${tokens.space.lg}px ${tokens.space.lg}px`,
          }}
        >
          {tab === "installed" && (
            <InstalledList
              skills={installed}
              loading={loading}
              onUninstall={uninstall}
            />
          )}
          {tab === "marketplace" && (
            <MarketplaceList
              skills={marketplace}
              loading={loading}
              onInstall={(s) => installFromUrl(s.source_url)}
            />
          )}
          {tab === "add-url" && (
            <AddByUrl
              value={urlInput}
              onChange={setUrlInput}
              onSubmit={() => installFromUrl(urlInput)}
              disabled={loading}
            />
          )}
        </div>

        {staged && (
          <ConfirmModal
            staged={staged}
            sensitive={sensitiveBadges}
            onApprove={() => confirmInstall(true)}
            onCancel={() => confirmInstall(false)}
          />
        )}
      </div>
    </div>
  );
};

// ---------- Installed list ----------

const EmptyState: React.FC<{ icon?: string; title: string; hint?: string }> = ({
  icon,
  title,
  hint,
}) => (
  <div
    style={{
      padding: `${tokens.space.xxl}px ${tokens.space.lg}px`,
      textAlign: "center",
      color: tokens.color.neutral[500],
    }}
  >
    {icon && <div style={{ fontSize: 32, marginBottom: tokens.space.sm }}>{icon}</div>}
    <div
      style={{
        fontSize: tokens.text.md.size,
        color: tokens.color.neutral[700],
        fontWeight: tokens.weight.medium,
      }}
    >
      {title}
    </div>
    {hint && (
      <div
        style={{
          fontSize: tokens.text.sm.size,
          color: tokens.color.neutral[500],
          marginTop: tokens.space.xs,
        }}
      >
        {hint}
      </div>
    )}
  </div>
);

const LoadingState: React.FC<{ label?: string }> = ({ label = "加载中…" }) => (
  <div
    style={{
      padding: `${tokens.space.xxl}px ${tokens.space.lg}px`,
      textAlign: "center",
      color: tokens.color.neutral[500],
      fontSize: tokens.text.sm.size,
    }}
  >
    <span className="bp-spinner" style={{ marginRight: tokens.space.sm }} />
    {label}
  </div>
);

const InstalledList: React.FC<{
  skills: SkillMeta[];
  loading: boolean;
  onUninstall: (name: string) => void;
}> = ({ skills, loading, onUninstall }) => {
  if (loading) return <LoadingState />;
  if (!skills.length)
    return (
      <EmptyState
        icon="📭"
        title="暂无已安装技能"
        hint="切换到「市场」或「通过 URL 安装」试试"
      />
    );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: tokens.space.sm }}>
      {skills.map((s) => (
        <div
          key={s.name}
          className="bp-card"
          style={{
            ...cardStyle,
            display: "flex",
            alignItems: "center",
            gap: tokens.space.md,
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: tokens.text.md.size,
                fontWeight: tokens.weight.semibold,
                color: tokens.color.neutral[900],
                display: "flex",
                alignItems: "center",
                gap: tokens.space.xs,
              }}
            >
              {s.name}
              {s.version && (
                <span
                  style={{
                    fontSize: tokens.text.xs.size,
                    color: tokens.color.neutral[500],
                    fontWeight: tokens.weight.regular,
                  }}
                >
                  v{s.version}
                </span>
              )}
            </div>
            {s.description && (
              <div
                style={{
                  fontSize: tokens.text.sm.size,
                  color: tokens.color.neutral[600],
                  marginTop: 2,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  display: "-webkit-box",
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: "vertical",
                }}
              >
                {s.description}
              </div>
            )}
          </div>
          <button
            type="button"
            className="bp-btn-secondary"
            onClick={() => onUninstall(s.name)}
            style={{
              ...buttonStyle("secondary", "sm"),
              color: tokens.color.danger.bg,
              borderColor: tokens.color.danger.border,
            }}
          >
            卸载
          </button>
        </div>
      ))}
    </div>
  );
};

// ---------- Marketplace list ----------

const MarketplaceList: React.FC<{
  skills: MarketplaceSkill[];
  loading: boolean;
  onInstall: (s: MarketplaceSkill) => void;
}> = ({ skills, loading, onInstall }) => {
  if (loading) return <LoadingState />;
  if (!skills.length)
    return (
      <EmptyState
        icon="🛒"
        title="暂无可用技能"
        hint="官方注册表暂未发布，可使用「通过 URL 安装」"
      />
    );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: tokens.space.sm }}>
      {skills.map((s) => (
        <div
          key={s.source_url}
          className="bp-card"
          style={{
            ...cardStyle,
            display: "flex",
            alignItems: "flex-start",
            gap: tokens.space.md,
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: tokens.text.md.size,
                fontWeight: tokens.weight.semibold,
                color: tokens.color.neutral[900],
              }}
            >
              {s.name}
              {s.author && (
                <span
                  style={{
                    fontSize: tokens.text.xs.size,
                    color: tokens.color.neutral[500],
                    fontWeight: tokens.weight.regular,
                    marginLeft: tokens.space.xs,
                  }}
                >
                  · {s.author}
                </span>
              )}
            </div>
            {/* Marketplace cards: clamp to 4 lines so a long description
                (e.g. docx skill ships ~1500 chars of "Triggers include …
                Word doc / .docx / professional documents …") doesn't
                expand the card to fill the entire scroll viewport,
                hiding the rest of the list. InstalledList already does
                this at line ~510. Keep both in sync. */}
            <div
              style={{
                fontSize: tokens.text.sm.size,
                color: tokens.color.neutral[600],
                marginTop: 4,
                lineHeight: 1.5,
                overflow: "hidden",
                textOverflow: "ellipsis",
                display: "-webkit-box",
                WebkitLineClamp: 4,
                WebkitBoxOrient: "vertical",
              }}
              title={s.description}
            >
              {s.description}
            </div>
            <div
              style={{
                fontSize: tokens.text.xs.size,
                color: tokens.color.neutral[400],
                marginTop: 6,
                fontFamily: tokens.font.mono,
                wordBreak: "break-all",
              }}
            >
              {s.source_url}
            </div>
            {(s.permission_categories || []).length > 0 && (
              <div
                style={{
                  marginTop: tokens.space.sm,
                  display: "flex",
                  gap: tokens.space.xs,
                  flexWrap: "wrap",
                }}
              >
                {(s.permission_categories || []).map((c) => (
                  <span
                    key={c}
                    style={badgeStyle(
                      SENSITIVE_CATS.includes(c) ? "error" : "warning"
                    )}
                  >
                    {c}
                  </span>
                ))}
              </div>
            )}
          </div>
          <button
            type="button"
            className="bp-btn-primary"
            onClick={() => onInstall(s)}
            style={buttonStyle("primary", "sm")}
          >
            安装
          </button>
        </div>
      ))}
    </div>
  );
};

// ---------- Add by URL ----------

const AddByUrl: React.FC<{
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled: boolean;
}> = ({ value, onChange, onSubmit, disabled }) => (
  <div>
    <div
      style={{
        ...bannerStyle("info"),
        marginBottom: tokens.space.md,
        fontSize: tokens.text.sm.size,
      }}
    >
      支持以下 GitHub 形式：
      <ul
        style={{
          margin: `${tokens.space.xs}px 0 0`,
          paddingLeft: tokens.space.lg,
          fontFamily: tokens.font.mono,
          fontSize: tokens.text.xs.size,
        }}
      >
        <li>github:owner/repo</li>
        <li>github:owner/repo/tree/branch/subpath</li>
        <li>https://github.com/owner/repo</li>
      </ul>
    </div>
    <input
      className="bp-input"
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder="github:anthropics/skills/tree/main/skills/algorithmic-art"
      style={inputStyle}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !disabled && value.trim()) {
          onSubmit();
        }
      }}
    />
    <div
      style={{
        marginTop: tokens.space.md,
        display: "flex",
        justifyContent: "flex-end",
      }}
    >
      <button
        type="button"
        className="bp-btn-primary"
        disabled={disabled || !value.trim()}
        onClick={onSubmit}
        style={buttonStyle("primary", "md", disabled || !value.trim())}
      >
        {disabled ? <span className="bp-spinner" /> : "安装"}
      </button>
    </div>
  </div>
);

// ---------- Confirm modal ----------

const ConfirmModal: React.FC<{
  staged: StagedSkill;
  sensitive: PermissionCategory[];
  onApprove: () => void;
  onCancel: () => void;
}> = ({ staged, sensitive, onApprove, onCancel }) => {
  const dangerous = sensitive.length > 0;
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: tokens.color.surface.lightBackdrop,
        backdropFilter: "blur(3px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 10,
        animation: `bp-fade-in ${tokens.duration.base}ms ${tokens.easing.out}`,
      }}
    >
      <div
        style={{
          ...surfaceLight,
          width: 480,
          maxWidth: "92%",
          borderTop: `4px solid ${
            dangerous ? tokens.color.danger.bg : tokens.color.accent.bg
          }`,
          padding: tokens.space.lg,
          animation: `bp-pop-in ${tokens.duration.base}ms ${tokens.easing.out}`,
        }}
      >
        <div
          style={{
            fontSize: tokens.text.xs.size,
            color: tokens.color.neutral[500],
            fontWeight: tokens.weight.medium,
            marginBottom: 2,
          }}
        >
          确认安装
        </div>
        <h3
          style={{
            margin: 0,
            fontSize: tokens.text.lg.size,
            fontWeight: tokens.weight.semibold,
            color: tokens.color.neutral[900],
          }}
        >
          {staged.name}
        </h3>
        <p
          style={{
            fontSize: tokens.text.md.size,
            color: tokens.color.neutral[700],
            marginTop: tokens.space.sm,
            marginBottom: tokens.space.sm,
            lineHeight: 1.55,
          }}
        >
          {(staged.manifest.description as string) || "(无描述)"}
        </p>
        {dangerous && (
          <div style={bannerStyle("error")}>
            ⚠ 该技能请求敏感权限：
            <strong style={{ marginLeft: 4 }}>{sensitive.join(", ")}</strong>
            。请确认来源可信。
          </div>
        )}
        <details style={{ marginTop: tokens.space.md, fontSize: tokens.text.sm.size }}>
          <summary
            style={{
              cursor: "pointer",
              userSelect: "none",
              color: tokens.color.neutral[500],
            }}
          >
            查看 manifest.json
          </summary>
          <pre
            style={{
              background: tokens.color.neutral[50],
              border: `1px solid ${tokens.color.neutral[200]}`,
              padding: tokens.space.sm,
              borderRadius: tokens.radius.md,
              fontFamily: tokens.font.mono,
              fontSize: tokens.text.xs.size,
              maxHeight: 200,
              overflow: "auto",
              margin: `${tokens.space.xs}px 0 0`,
            }}
          >
            {JSON.stringify(staged.manifest, null, 2)}
          </pre>
        </details>
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: tokens.space.sm,
            marginTop: tokens.space.lg,
          }}
        >
          <button
            type="button"
            className="bp-btn-secondary"
            onClick={onCancel}
            style={buttonStyle("secondary", "md")}
          >
            取消
          </button>
          <button
            type="button"
            className={dangerous ? "bp-btn-danger" : "bp-btn-primary"}
            onClick={onApprove}
            style={buttonStyle(dangerous ? "danger" : "primary", "md")}
          >
            {dangerous ? "我明白风险，继续安装" : "确认安装"}
          </button>
        </div>
      </div>
    </div>
  );
};

export default SkillStorePanel;
