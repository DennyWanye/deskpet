/**
 * WI-01 (beta-100) — first-run onboarding wizard.
 *
 * Three steps, each skippable:
 *   1. Welcome — what DeskPet is.
 *   2. Connect a model — base_url / model / api_key + a "test
 *      connection" button. "Next" stays disabled until the test passes;
 *      a successful test also persists the config (testing == saving in
 *      the onboarding flow, so the user isn't asked to click Save
 *      separately).
 *   3. Local capability — explains the BGE-M3 memory model downloads in
 *      the background; memory degrades to mock until then.
 *
 * The component is **presentation + local state only**. All side
 * effects (test connection, persist completion) are injected as props
 * so the wizard is unit-testable without Tauri.
 */
import { memo, useState, useCallback } from "react";

export interface OnboardingConfig {
  base_url: string;
  model: string;
  api_key: string;
}

export interface OnboardingWizardProps {
  /** Test (and persist) the LLM connection. Resolves ok=false on failure. */
  onTestConnection: (
    cfg: OnboardingConfig,
  ) => Promise<{ ok: boolean; error?: string }>;
  /** Called when the user finishes the wizard. Host writes the marker. */
  onComplete: () => void;
  /** Called when the user skips at any step. Host still writes the marker. */
  onSkip: () => void;
}

export type OnboardingStep = 1 | 2 | 3;
export type OnboardingTestState = "idle" | "testing" | "ok" | "failed";

// Re-export under the old local names so the component body below is
// unchanged.
type Step = OnboardingStep;
type TestState = OnboardingTestState;

/**
 * Pure: may the user advance from `step`?
 *  - step 1 → always (just an intro)
 *  - step 2 → only when the connection test passed (testState === "ok")
 *  - step 3 → no "next" (it's the last step; the host shows "完成")
 * Exported so it can be unit-tested without a DOM.
 */
export function nextStepAllowed(
  step: OnboardingStep,
  testState: OnboardingTestState,
): boolean {
  if (step === 2) return testState === "ok";
  return step < 3;
}

function OnboardingWizardImpl({
  onTestConnection,
  onComplete,
  onSkip,
}: OnboardingWizardProps) {
  const [step, setStep] = useState<Step>(1);
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

  const canLeaveStep2 = nextStepAllowed(2, testState);

  return (
    <div data-testid="onboarding-wizard" style={overlayStyle}>
      <div style={cardStyle} role="dialog" aria-label="DeskPet 初次设置">
        {/* Step indicator */}
        <div style={stepBarStyle}>
          {[1, 2, 3].map((n) => (
            <div
              key={n}
              data-testid={`step-dot-${n}`}
              style={{
                ...dotStyle,
                background: n <= step ? "#2563eb" : "#cbd5e1",
              }}
            />
          ))}
        </div>

        {step === 1 && (
          <div data-testid="onboarding-step-1">
            <h2 style={titleStyle}>欢迎使用 DeskPet 🐾</h2>
            <p style={bodyStyle}>
              DeskPet 是一只住在你桌面上的 AI 桌宠。它能陪你聊天、记住你说过的事、
              帮你做 PPT、查资料，还能进入"代码模式"帮你写程序。
            </p>
            <p style={bodyStyle}>
              接下来只需 2 步就能开始 —— 整个过程不到 1 分钟。
            </p>
          </div>
        )}

        {step === 2 && (
          <div data-testid="onboarding-step-2">
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
                ✓ 连接成功，配置已保存
              </p>
            )}
            {testState === "failed" && (
              <p data-testid="onboarding-test-error" style={errStyle}>
                ✗ {testError}
              </p>
            )}
          </div>
        )}

        {step === 3 && (
          <div data-testid="onboarding-step-3">
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
          {step > 1 && (
            <button
              data-testid="onboarding-back-btn"
              style={secondaryBtnStyle}
              onClick={() => setStep((s) => (s - 1) as Step)}
            >
              上一步
            </button>
          )}
          {step < 3 && (
            <button
              data-testid="onboarding-next-btn"
              style={primaryBtnStyle}
              disabled={step === 2 && !canLeaveStep2}
              onClick={() => setStep((s) => (s + 1) as Step)}
            >
              下一步
            </button>
          )}
          {step === 3 && (
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
  background: "rgba(15, 23, 42, 0.55)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 9999,
  backdropFilter: "blur(4px)",
};

const cardStyle: React.CSSProperties = {
  width: 460,
  maxWidth: "92vw",
  background: "#ffffff",
  borderRadius: 16,
  padding: "26px 30px 20px",
  boxShadow: "0 24px 60px rgba(0,0,0,0.32)",
  fontFamily: "Microsoft YaHei UI, sans-serif",
  color: "#0f172a",
};

const stepBarStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  justifyContent: "center",
  marginBottom: 18,
};

const dotStyle: React.CSSProperties = {
  width: 28,
  height: 6,
  borderRadius: 3,
  transition: "background 200ms",
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
  padding: "8px 10px",
  fontSize: 13,
  border: "1px solid #cbd5e1",
  borderRadius: 8,
  outline: "none",
};

const testBtnStyle: React.CSSProperties = {
  marginTop: 14,
  padding: "8px 16px",
  fontSize: 13,
  fontWeight: 600,
  background: "#0f172a",
  color: "#fff",
  border: "none",
  borderRadius: 8,
  cursor: "pointer",
};

const okStyle: React.CSSProperties = {
  color: "#16a34a",
  fontSize: 13,
  fontWeight: 600,
  marginTop: 10,
};

const errStyle: React.CSSProperties = {
  color: "#dc2626",
  fontSize: 13,
  fontWeight: 600,
  marginTop: 10,
};

const footerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginTop: 22,
};

const primaryBtnStyle: React.CSSProperties = {
  padding: "8px 20px",
  fontSize: 13,
  fontWeight: 700,
  background: "#2563eb",
  color: "#fff",
  border: "none",
  borderRadius: 8,
  cursor: "pointer",
};

const secondaryBtnStyle: React.CSSProperties = {
  padding: "8px 16px",
  fontSize: 13,
  fontWeight: 600,
  background: "#e2e8f0",
  color: "#334155",
  border: "none",
  borderRadius: 8,
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
