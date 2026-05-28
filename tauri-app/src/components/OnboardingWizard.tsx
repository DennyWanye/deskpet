// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WI-01 (beta-100) — first-run onboarding wizard.
 *
 * WI-R3 (relay edition) — the step list is now **data-driven** instead
 * of a hard-coded `1 | 2 | 3`. `stepsForEdition()` returns the ordered
 * step list for the active build edition:
 *
 *   - manual / null edition → [welcome, connectModel, ready]  (3 steps,
 *     byte-identical to the original WI-01 behaviour)
 *   - relay edition         → [welcome, ready]                (2 steps —
 *     the LLM is auto-configured by login, so there is no "手填模型"
 *     step; see RelayAuthAdapter / relayProviderBridge)
 *
 * Step-dot rendering, prev/next navigation and `nextStepAllowed` all key
 * off the array + the current index — adding/removing a step never again
 * means touching a `1 | 2 | 3` union.
 *
 * The component is **presentation + local state only**. All side
 * effects (test connection, persist completion) are injected as props
 * so the wizard is unit-testable without Tauri.
 */
import { memo, useMemo, useState, useCallback } from "react";

import { Icon } from "./Icon";

export interface OnboardingConfig {
  base_url: string;
  model: string;
  api_key: string;
}

export interface OnboardingWizardProps {
  /** Test (and persist) the LLM connection. Resolves ok=false on failure.
   *  Unused in relay edition (no connectModel step). */
  onTestConnection: (
    cfg: OnboardingConfig,
  ) => Promise<{ ok: boolean; error?: string }>;
  /** Called when the user finishes the wizard. Host writes the marker. */
  onComplete: () => void;
  /** Called when the user skips at any step. Host still writes the marker. */
  onSkip: () => void;
  /** Build edition — selects the step list. Defaults to "manual". */
  edition?: string;
}

export type OnboardingTestState = "idle" | "testing" | "ok" | "failed";

/** The distinct onboarding step kinds. */
export type OnboardingStepId = "welcome" | "connectModel" | "ready";

export interface OnboardingStepDef {
  id: OnboardingStepId;
}

/**
 * Ordered step list for an edition. relay edition drops `connectModel`
 * (login auto-configures the model). Pure — unit-testable.
 */
export function stepsForEdition(edition?: string): OnboardingStepDef[] {
  if (edition === "relay") {
    return [{ id: "welcome" }, { id: "ready" }];
  }
  return [{ id: "welcome" }, { id: "connectModel" }, { id: "ready" }];
}

/**
 * Pure: may the user advance from the step at `index` of `steps`?
 *  - last step                → false (host shows "完成", not "下一步")
 *  - a `connectModel` step     → only when the connection test passed
 *  - any other step            → always (intro-style steps)
 * Exported so it can be unit-tested without a DOM.
 */
export function nextStepAllowed(
  steps: OnboardingStepDef[],
  index: number,
  testState: OnboardingTestState,
): boolean {
  const step = steps[index];
  if (!step) return false;
  if (index >= steps.length - 1) return false;
  if (step.id === "connectModel") return testState === "ok";
  return true;
}

type TestState = OnboardingTestState;

function OnboardingWizardImpl({
  onTestConnection,
  onComplete,
  onSkip,
  edition,
}: OnboardingWizardProps) {
  const steps = useMemo(() => stepsForEdition(edition), [edition]);
  const [index, setIndex] = useState(0);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [testState, setTestState] = useState<TestState>("idle");
  const [testError, setTestError] = useState<string>("");

  const handleTest = useCallback(async () => {
    setTestState("testing");
    setTestError("");
    try {
      const res = await onTestConnection({
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey.trim(),
      });
      if (res.ok) {
        setTestState("ok");
      } else {
        setTestState("failed");
        setTestError(res.error || "连接失败，请检查地址 / 模型 / 密钥");
      }
    } catch (e) {
      setTestState("failed");
      setTestError(String(e));
    }
  }, [onTestConnection, baseUrl, model, apiKey]);

  const currentStep = steps[index];
  const isLast = index >= steps.length - 1;
  const canAdvance = nextStepAllowed(steps, index, testState);

  return (
    <div data-testid="onboarding-wizard" style={overlayStyle}>
      <div style={cardStyle} role="dialog" aria-label="DeskPet 初次设置">
        {/* Step indicator — one dot per step in the active edition's list */}
        <div style={stepBarStyle}>
          {steps.map((s, n) => (
            <div
              key={s.id}
              data-testid={`step-dot-${n + 1}`}
              style={{
                ...dotStyle,
                background: n <= index ? "#2563eb" : "#cbd5e1",
              }}
            />
          ))}
        </div>

        {currentStep?.id === "welcome" && (
          <div data-testid="onboarding-step-welcome">
            <h2 style={titleStyle}>欢迎使用 DeskPet 🐾</h2>
            <p style={bodyStyle}>
              DeskPet 是一只住在你桌面上的 AI 桌宠。它能陪你聊天、记住你说过的事、
              帮你做 PPT、查资料，还能进入"代码模式"帮你写程序。
            </p>
            <p style={bodyStyle}>很快就能开始 —— 整个过程不到 1 分钟。</p>
          </div>
        )}

        {currentStep?.id === "connectModel" && (
          <div data-testid="onboarding-step-connectModel">
            <h2 style={titleStyle}>接入大模型</h2>
            <p style={bodyStyle}>
              DeskPet 的"大脑"需要一个大语言模型。填入你的服务地址、模型名和密钥，
              点"测试连接"验证后即可继续。
            </p>
            <label style={labelStyle}>
              服务地址 (base_url)
              <input
                data-testid="onboarding-base-url"
                style={inputStyle}
                value={baseUrl}
                placeholder="https://api.example.com/v1"
                onChange={(e) => {
                  setBaseUrl(e.target.value);
                  setTestState("idle");
                }}
              />
            </label>
            <label style={labelStyle}>
              模型名 (model)
              <input
                data-testid="onboarding-model"
                style={inputStyle}
                value={model}
                placeholder="gpt-4o / deepseek-chat / ..."
                onChange={(e) => {
                  setModel(e.target.value);
                  setTestState("idle");
                }}
              />
            </label>
            <label style={labelStyle}>
              密钥 (api_key)
              <input
                data-testid="onboarding-api-key"
                style={inputStyle}
                type="password"
                value={apiKey}
                placeholder="sk-..."
                onChange={(e) => {
                  setApiKey(e.target.value);
                  setTestState("idle");
                }}
              />
            </label>
            <button
              data-testid="onboarding-test-btn"
              style={testBtnStyle}
              disabled={testState === "testing"}
              onClick={handleTest}
            >
              {testState === "testing" ? "测试中…" : "测试连接"}
            </button>
            {testState === "ok" && (
              <p data-testid="onboarding-test-ok" style={okStyle}>
                <Icon name="check" size={15} />
                连接成功，配置已保存
              </p>
            )}
            {testState === "failed" && (
              <p data-testid="onboarding-test-error" style={errStyle}>
                <Icon name="alert" size={15} style={{ flexShrink: 0, marginTop: 1 }} />
                <span>{testError}</span>
              </p>
            )}
          </div>
        )}

        {currentStep?.id === "ready" && (
          <div data-testid="onboarding-step-ready">
            <h2 style={titleStyle}>本地记忆能力</h2>
            <p style={bodyStyle}>
              DeskPet 会在后台下载一个本地记忆模型 (BGE-M3，约 286MB)，
              用来记住你和它的对话。
            </p>
            <p style={bodyStyle}>
              下载完成前，记忆功能会以"轻量模式"运行 —— 不影响聊天，
              只是长期记忆会稍弱一些。下载完成后自动切换，无需任何操作。
            </p>
            <p style={bodyStyle}>一切就绪，开始和 DeskPet 玩吧！</p>
          </div>
        )}

        {/* Footer buttons */}
        <div style={footerStyle}>
          <button
            data-testid="onboarding-skip-btn"
            style={skipBtnStyle}
            onClick={onSkip}
          >
            跳过
          </button>
          <div style={{ flex: 1 }} />
          {index > 0 && (
            <button
              data-testid="onboarding-back-btn"
              style={secondaryBtnStyle}
              onClick={() => setIndex((i) => Math.max(0, i - 1))}
            >
              上一步
            </button>
          )}
          {!isLast && (
            <button
              data-testid="onboarding-next-btn"
              style={primaryBtnStyle}
              disabled={!canAdvance}
              onClick={() =>
                setIndex((i) => Math.min(steps.length - 1, i + 1))
              }
            >
              下一步
            </button>
          )}
          {isLast && (
            <button
              data-testid="onboarding-finish-btn"
              style={primaryBtnStyle}
              onClick={onComplete}
            >
              完成
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export const OnboardingWizard = memo(OnboardingWizardImpl);

// --------------------------- styles ----------------------------------

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(8,11,20,0.58)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 9999,
  backdropFilter: "blur(7px)",
  WebkitBackdropFilter: "blur(7px)",
};

const cardStyle: React.CSSProperties = {
  width: 460,
  maxWidth: "92vw",
  background: "#ffffff",
  borderRadius: 20,
  padding: "28px 32px 22px",
  border: "1px solid rgba(255,255,255,0.6)",
  boxShadow:
    "0 32px 70px rgba(8,11,20,0.45), 0 4px 14px rgba(8,11,20,0.18)",
  fontFamily:
    '"Inter","PingFang SC","Microsoft YaHei UI",sans-serif',
  color: "#0f172a",
  animation: "bp-pop-in 280ms cubic-bezier(0.16,1,0.3,1)",
};

const stepBarStyle: React.CSSProperties = {
  display: "flex",
  gap: 7,
  justifyContent: "center",
  marginBottom: 20,
};

const dotStyle: React.CSSProperties = {
  width: 30,
  height: 5,
  borderRadius: 999,
  transition: "background 220ms ease",
};

const titleStyle: React.CSSProperties = {
  fontSize: 22,
  fontWeight: 700,
  margin: "0 0 12px",
};

const bodyStyle: React.CSSProperties = {
  fontSize: 14,
  lineHeight: 1.7,
  color: "#475569",
  margin: "0 0 10px",
};

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 12.5,
  fontWeight: 600,
  color: "#334155",
  margin: "12px 0 4px",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  marginTop: 4,
  padding: "9px 12px",
  fontSize: 13,
  border: "1px solid #e2e8f0",
  borderRadius: 10,
  background: "#f8fafc",
  outline: "none",
  color: "#0f172a",
};

const testBtnStyle: React.CSSProperties = {
  marginTop: 14,
  padding: "9px 18px",
  fontSize: 13,
  fontWeight: 700,
  background: "linear-gradient(180deg,#1e293b,#0f172a)",
  color: "#fff",
  border: "1px solid rgba(15,23,42,0.6)",
  borderRadius: 10,
  cursor: "pointer",
};

const okStyle: React.CSSProperties = {
  color: "#16a34a",
  fontSize: 13,
  fontWeight: 600,
  marginTop: 10,
  display: "flex",
  alignItems: "center",
  gap: 6,
};

const errStyle: React.CSSProperties = {
  color: "#dc2626",
  fontSize: 13,
  fontWeight: 600,
  marginTop: 10,
  display: "flex",
  alignItems: "flex-start",
  gap: 6,
  lineHeight: 1.5,
};

const footerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginTop: 22,
};

const primaryBtnStyle: React.CSSProperties = {
  padding: "9px 22px",
  fontSize: 13,
  fontWeight: 700,
  background: "linear-gradient(180deg,#3b82f6,#2563eb)",
  color: "#fff",
  border: "1px solid rgba(37,99,235,0.5)",
  borderRadius: 10,
  boxShadow: "0 6px 16px rgba(37,99,235,0.30)",
  cursor: "pointer",
};

const secondaryBtnStyle: React.CSSProperties = {
  padding: "9px 16px",
  fontSize: 13,
  fontWeight: 600,
  background: "#ffffff",
  color: "#334155",
  border: "1px solid #e2e8f0",
  borderRadius: 10,
  cursor: "pointer",
};

const skipBtnStyle: React.CSSProperties = {
  padding: "8px 12px",
  fontSize: 12.5,
  background: "transparent",
  color: "#94a3b8",
  border: "none",
  cursor: "pointer",
  textDecoration: "underline",
};
