// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import React, { useEffect, useRef, useState } from "react";

import type { ClarificationRequest } from "../types/skillPlatform";
import {
  backdropStyle,
  buttonStyle,
  inputStyle,
  surfaceLight,
} from "../theme/components";
import { tokens } from "../theme/tokens";

interface Props {
  current: ClarificationRequest["payload"] | null;
  onResolve: (answer: string) => void;
}

export const ClarificationDialog: React.FC<Props> = ({
  current,
  onResolve,
}) => {
  const [answer, setAnswer] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!current) return;
    setAnswer("");
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [current]);

  if (!current) return null;

  const submit = () => {
    onResolve(answer.trim());
  };

  const options = Array.isArray(current.options) ? current.options : [];

  return (
    <div
      style={{ ...backdropStyle, zIndex: 9998 }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="clarification-dialog-title"
    >
      <div
        style={{
          ...surfaceLight,
          width: 440,
          maxWidth: "92vw",
          maxHeight: "92vh",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          animation: `bp-pop-in ${tokens.duration.base}ms ${tokens.easing.out}`,
          borderTop: `4px solid ${tokens.color.accent.bg}`,
        }}
      >
        <div
          style={{
            padding: `${tokens.space.lg}px ${tokens.space.lg}px ${tokens.space.sm}px`,
          }}
        >
          <div
            style={{
              fontSize: tokens.text.xs.size,
              color: tokens.color.neutral[500],
              fontWeight: tokens.weight.medium,
              marginBottom: tokens.space.xs,
            }}
          >
            澄清请求
          </div>
          <h3
            id="clarification-dialog-title"
            style={{
              margin: 0,
              fontSize: tokens.text.lg.size,
              fontWeight: tokens.weight.semibold,
              color: tokens.color.neutral[900],
              lineHeight: 1.35,
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
            }}
          >
            {current.question}
          </h3>
        </div>

        <div
          style={{
            padding: `0 ${tokens.space.lg}px ${tokens.space.lg}px`,
            display: "flex",
            flexDirection: "column",
            gap: tokens.space.md,
          }}
        >
          {options.length > 0 && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: tokens.space.sm,
              }}
            >
              {options.map((option) => (
                <button
                  key={option}
                  type="button"
                  className="bp-btn-secondary"
                  onClick={() => onResolve(option)}
                  style={{
                    ...buttonStyle("secondary", "md"),
                    justifyContent: "flex-start",
                    lineHeight: 1.35,
                    whiteSpace: "normal",
                    textAlign: "left",
                    width: "100%",
                  }}
                >
                  {option}
                </button>
              ))}
            </div>
          )}

          <div
            style={{
              display: "flex",
              gap: tokens.space.sm,
              alignItems: "stretch",
            }}
          >
            <input
              ref={inputRef}
              value={answer}
              onChange={(event) => setAnswer(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  submit();
                }
              }}
              style={{ ...inputStyle, flex: 1 }}
              aria-label="澄清回答"
            />
            <button
              type="button"
              className="bp-btn-primary"
              onClick={submit}
              style={buttonStyle("primary", "md")}
            >
              确认
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ClarificationDialog;
