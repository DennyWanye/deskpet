/**
 * P4-S20 Stage C — SkillStorePanel
 *
 * Three tabs: Installed / Marketplace / Add by URL.
 * Talks to backend via the control WS:
 *   - skill_marketplace_list / skill_marketplace_list_response
 *   - skill_list_installed / skill_list_installed_response
 *   - skill_install_from_url / skill_install_pending
 *   - skill_install_confirm / skill_install_confirm_response
 *   - skill_uninstall / skill_uninstall_response
 *
 * Spec: openspec/changes/deskpet-skill-platform/specs/skill-marketplace/spec.md
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";

import type { ControlChannel } from "../ws/ControlChannel";
import type {
  MarketplaceSkill,
  SkillMeta,
  PermissionCategory,
} from "../types/skillPlatform";

type Tab = "installed" | "marketplace" | "add-url";

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

export const SkillStorePanel: React.FC<Props> = ({ open, channel, onClose }) => {
  const [tab, setTab] = useState<Tab>("installed");
  const [installed, setInstalled] = useState<SkillMeta[]>([]);
  const [marketplace, setMarketplace] = useState<MarketplaceSkill[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [urlInput, setUrlInput] = useState("");
  const [staged, setStaged] = useState<StagedSkill | null>(null);
  const [loading, setLoading] = useState(false);

  // Listen for backend responses
  useEffect(() => {
    if (!open || !channel) return undefined;
    const off = channel.onMessage((msg) => {
      const t = (msg as { type?: string }).type;
      const payload = (msg as { payload?: Record<string, unknown> }).payload || {};
      if (t === "skill_marketplace_list_response") {
        setMarketplace((payload.skills as MarketplaceSkill[]) || []);
        if (payload.error) setErrorMsg(String(payload.error));
        setLoading(false);
      } else if (t === "skill_list_installed_response") {
        setInstalled((payload.skills as SkillMeta[]) || []);
        if (payload.error) setErrorMsg(String(payload.error));
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
          setErrorMsg(String(payload.error || "install failed"));
        }
      } else if (t === "skill_install_confirm_response") {
        setLoading(false);
        if (payload.ok) {
          setStaged(null);
          setUrlInput("");
          // refresh installed
          channel.send({ type: "skill_list_installed" });
          setTab("installed");
        } else {
          setErrorMsg(String(payload.error || payload.reason || "install failed"));
        }
      } else if (t === "skill_uninstall_response") {
        setLoading(false);
        if (payload.ok) {
          channel.send({ type: "skill_list_installed" });
        } else {
          setErrorMsg(String(payload.error || "uninstall failed"));
        }
      }
    });
    return () => off();
  }, [open, channel]);

  // Refresh on tab change / panel open
  useEffect(() => {
    if (!open || !channel) return;
    setErrorMsg(null);
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
      setErrorMsg(null);
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
      if (!confirm(`Uninstall '${name}'?`)) return;
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
        background: "rgba(0,0,0,0.5)",
        zIndex: 9000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="skill-store-title"
    >
      <div
        style={{
          background: "white",
          borderRadius: 12,
          padding: 20,
          width: 720,
          maxWidth: "94vw",
          maxHeight: "86vh",
          display: "flex",
          flexDirection: "column",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h2 id="skill-store-title" style={{ margin: 0, fontSize: 18 }}>
            技能商店 SkillStore
          </h2>
          <button
            type="button"
            onClick={onClose}
            style={{
              marginLeft: "auto",
              border: "none",
              background: "transparent",
              fontSize: 18,
              cursor: "pointer",
            }}
            aria-label="关闭"
          >
            ×
          </button>
        </div>
        <div style={{ display: "flex", gap: 4, marginTop: 12 }}>
          {(["installed", "marketplace", "add-url"] as Tab[]).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              style={{
                padding: "6px 12px",
                border: "1px solid #d1d5db",
                background: tab === t ? "#3b82f6" : "white",
                color: tab === t ? "white" : "#1f2937",
                borderRadius: 6,
                cursor: "pointer",
              }}
            >
              {t === "installed"
                ? "已安装"
                : t === "marketplace"
                ? "市场"
                : "通过 URL 安装"}
            </button>
          ))}
        </div>

        {errorMsg && (
          <div
            style={{
              marginTop: 8,
              padding: 8,
              background: "#fee2e2",
              color: "#991b1b",
              borderRadius: 4,
              fontSize: 12,
            }}
            role="alert"
          >
            {errorMsg}
          </div>
        )}

        <div style={{ overflow: "auto", flex: 1, marginTop: 12 }}>
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

const InstalledList: React.FC<{
  skills: SkillMeta[];
  loading: boolean;
  onUninstall: (name: string) => void;
}> = ({ skills, loading, onUninstall }) => {
  if (loading) return <p>加载中…</p>;
  if (!skills.length) return <p style={{ color: "#6b7280" }}>暂无已安装技能。</p>;
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {skills.map((s) => (
        <li
          key={s.name}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "10px 0",
            borderBottom: "1px solid #e5e7eb",
          }}
        >
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600 }}>{s.name}</div>
            <div style={{ fontSize: 12, color: "#6b7280" }}>
              {s.description}
            </div>
            {s.version && (
              <div style={{ fontSize: 11, color: "#9ca3af" }}>v{s.version}</div>
            )}
          </div>
          <button
            type="button"
            onClick={() => onUninstall(s.name)}
            style={btnGhostStyle("#dc2626")}
          >
            卸载
          </button>
        </li>
      ))}
    </ul>
  );
};

const MarketplaceList: React.FC<{
  skills: MarketplaceSkill[];
  loading: boolean;
  onInstall: (s: MarketplaceSkill) => void;
}> = ({ skills, loading, onInstall }) => {
  if (loading) return <p>加载中…</p>;
  if (!skills.length) return <p style={{ color: "#6b7280" }}>暂无可用技能。</p>;
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {skills.map((s) => (
        <li
          key={s.source_url}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "10px 0",
            borderBottom: "1px solid #e5e7eb",
          }}
        >
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600 }}>{s.name}</div>
            <div style={{ fontSize: 12, color: "#6b7280" }}>{s.description}</div>
            <div style={{ fontSize: 11, color: "#9ca3af" }}>{s.source_url}</div>
            {(s.permission_categories || []).length > 0 && (
              <div style={{ marginTop: 4, display: "flex", gap: 4, flexWrap: "wrap" }}>
                {(s.permission_categories || []).map((c) => (
                  <span
                    key={c}
                    style={{
                      fontSize: 10,
                      padding: "2px 6px",
                      borderRadius: 4,
                      background: SENSITIVE_CATS.includes(c) ? "#fee2e2" : "#fef3c7",
                      color: SENSITIVE_CATS.includes(c) ? "#991b1b" : "#92400e",
                    }}
                  >
                    {c}
                  </span>
                ))}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={() => onInstall(s)}
            style={btnSolidStyle("#2563eb")}
          >
            安装
          </button>
        </li>
      ))}
    </ul>
  );
};

const AddByUrl: React.FC<{
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled: boolean;
}> = ({ value, onChange, onSubmit, disabled }) => (
  <div>
    <p style={{ fontSize: 13, color: "#6b7280" }}>
      支持以下格式：
      <br />
      • <code>github:owner/repo</code>
      <br />
      • <code>github:owner/repo/tree/main/subpath</code>
      <br />
      • <code>https://github.com/owner/repo</code>
    </p>
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder="github:foo/bar"
      style={{
        width: "100%",
        padding: 8,
        fontSize: 14,
        border: "1px solid #d1d5db",
        borderRadius: 6,
        marginTop: 8,
      }}
    />
    <button
      type="button"
      disabled={disabled || !value.trim()}
      onClick={onSubmit}
      style={{
        ...btnSolidStyle("#2563eb"),
        marginTop: 8,
        opacity: disabled || !value.trim() ? 0.5 : 1,
      }}
    >
      安装
    </button>
  </div>
);

const ConfirmModal: React.FC<{
  staged: StagedSkill;
  sensitive: PermissionCategory[];
  onApprove: () => void;
  onCancel: () => void;
}> = ({ staged, sensitive, onApprove, onCancel }) => (
  <div
    style={{
      position: "absolute",
      inset: 0,
      background: "rgba(0,0,0,0.45)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      zIndex: 10,
    }}
  >
    <div
      style={{
        background: "white",
        borderRadius: 8,
        padding: 16,
        width: 480,
        maxWidth: "90%",
        borderTop: `4px solid ${sensitive.length ? "#dc2626" : "#d97706"}`,
      }}
    >
      <h3 style={{ margin: 0, fontSize: 16 }}>
        确认安装 · {staged.name}
      </h3>
      <p style={{ fontSize: 13, color: "#1f2937", marginTop: 8 }}>
        {(staged.manifest.description as string) || "(no description)"}
      </p>
      {sensitive.length > 0 && (
        <div
          style={{
            marginTop: 8,
            padding: 8,
            background: "#fee2e2",
            color: "#991b1b",
            borderRadius: 4,
            fontSize: 13,
          }}
        >
          ⚠ 此技能请求敏感权限：{sensitive.join(", ")}
        </div>
      )}
      <details style={{ marginTop: 8, fontSize: 12 }}>
        <summary>查看 manifest.json</summary>
        <pre
          style={{
            background: "#f3f4f6",
            padding: 8,
            borderRadius: 4,
            fontSize: 11,
            maxHeight: 200,
            overflow: "auto",
          }}
        >
          {JSON.stringify(staged.manifest, null, 2)}
        </pre>
      </details>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
        <button type="button" onClick={onCancel} style={btnGhostStyle("#1f2937")}>
          取消
        </button>
        <button
          type="button"
          onClick={onApprove}
          style={btnSolidStyle(sensitive.length ? "#dc2626" : "#2563eb")}
        >
          确认安装
        </button>
      </div>
    </div>
  </div>
);

function btnSolidStyle(bg: string): React.CSSProperties {
  return {
    background: bg,
    color: "white",
    border: "none",
    padding: "6px 14px",
    borderRadius: 6,
    fontSize: 13,
    cursor: "pointer",
  };
}

function btnGhostStyle(fg: string): React.CSSProperties {
  return {
    background: "white",
    color: fg,
    border: `1px solid ${fg}`,
    padding: "6px 14px",
    borderRadius: 6,
    fontSize: 13,
    cursor: "pointer",
  };
}

export default SkillStorePanel;
