/**
 * P4-S23 — code block renderer.
 *
 * react-syntax-highlighter's Prism *light* build has been shipping
 * a broken `register` export in recent versions; trying to register
 * any language at module-load time throws `Prism.register is not a
 * function`. That used to crash the panel root → vite triggered
 * full-page reload → WebSocket reconnect storm.
 *
 * We sidestep the whole light-build mess by using the bundle's
 * default async `Prism` export, which loads languages on demand
 * via Prism core (lazy + correct API). It's slightly bigger
 * (around +60 KB gzipped vs light) but reliably highlights every
 * language without registration footwork.
 */
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
// @ts-ignore — RSH ships its own d.ts but the styles dir is implicit
import vscDarkPlus from "react-syntax-highlighter/dist/esm/styles/prism/vsc-dark-plus";

interface Props {
  language?: string;
  children: string;
}

export function CodeBlock({ language, children }: Props) {
  // Strip trailing newline that markdown parsers commonly tack on.
  const code = children.replace(/\n$/, "");
  return (
    <div style={{ position: "relative", margin: "8px 0" }}>
      {language && (
        <span
          style={{
            position: "absolute",
            top: 4,
            right: 8,
            fontSize: 10,
            color: "#94a3b8",
            opacity: 0.7,
          }}
        >
          {language}
        </span>
      )}
      <SyntaxHighlighter
        language={language || "text"}
        style={vscDarkPlus}
        customStyle={{
          margin: 0,
          padding: "10px 12px",
          fontSize: 12.5,
          lineHeight: 1.5,
          borderRadius: 6,
          background: "#1e1e1e",
        }}
        wrapLongLines
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}

/** Inline `code` (no language, single line). */
export function InlineCode({ children }: { children: React.ReactNode }) {
  return (
    <code
      style={{
        background: "rgba(148, 163, 184, 0.16)",
        color: "#e2e8f0",
        padding: "1px 5px",
        borderRadius: 3,
        fontSize: "0.92em",
        fontFamily:
          'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
      }}
    >
      {children}
    </code>
  );
}
