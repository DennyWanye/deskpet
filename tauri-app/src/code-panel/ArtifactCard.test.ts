// TG-5 部分 — ArtifactCard 纯函数测试（WI-T1.4 / WI-T1.7）
//
// vitest 当前为 node env（无 DOM），DOM 渲染/点击测试留 jsdom follow-up；
// 本文件覆盖：
//   - extractArtifactsFromResult 各种 result JSON 形态（核心解析逻辑）
//   - 字节级回落：无 artifacts 字段 → 空数组（TG-5 T5-5 守护）
//
// MR-22 埋点链路（emitArtifactAction）需要 DOM 才能测 button click，
// 留 follow-up。
import { describe, expect, it } from "vitest";
import { extractArtifactsFromResult, type ToolArtifact } from "./ArtifactCard";

describe("extractArtifactsFromResult — envelope 解析", () => {
  // T5-5 字节级硬保证：无 artifacts → []
  it("returns [] when result has no artifacts field", () => {
    const r = JSON.stringify({ ok: true, path: "/tmp/x.pptx" });
    expect(extractArtifactsFromResult(r)).toEqual([]);
  });

  // 显式 envelope.artifacts[]（registry 包装后）
  it("extracts envelope.artifacts[] (registry-wrapped path)", () => {
    const r = JSON.stringify({
      ok: true,
      result: "{}",
      artifacts: [
        { kind: "file", path: "C:\\out\\x.pptx", title: "x.pptx" },
      ],
    });
    const got = extractArtifactsFromResult(r);
    expect(got).toHaveLength(1);
    expect(got[0].kind).toBe("file");
    expect(got[0].path).toBe("C:\\out\\x.pptx");
  });

  // dry_run 嵌套路径：envelope.result 是 JSON 字符串，里面才含 artifacts
  it("extracts artifacts from nested envelope.result string", () => {
    const inner = JSON.stringify({
      ok: true,
      dry_run: true,
      artifacts: [
        { kind: "text", title: "preview", preview: "## md" },
      ],
    });
    const envelope = JSON.stringify({ ok: true, result: inner, error: null });
    const got = extractArtifactsFromResult(envelope);
    expect(got).toHaveLength(1);
    expect(got[0].kind).toBe("text");
    expect(got[0].preview).toBe("## md");
  });

  it("returns [] on malformed JSON", () => {
    expect(extractArtifactsFromResult("not json {{{")).toEqual([]);
  });

  it("returns [] on null / primitive result", () => {
    expect(extractArtifactsFromResult("null")).toEqual([]);
    expect(extractArtifactsFromResult("42")).toEqual([]);
    expect(extractArtifactsFromResult('"text"')).toEqual([]);
  });

  it("returns [] when artifacts field is not an array", () => {
    const r = JSON.stringify({ ok: true, artifacts: "not-an-array" });
    expect(extractArtifactsFromResult(r)).toEqual([]);
  });

  it("preserves multiple artifacts in order", () => {
    const r = JSON.stringify({
      ok: true,
      artifacts: [
        { kind: "file", path: "/a.pptx", title: "a" },
        { kind: "file", path: "/b.xlsx", title: "b" },
        { kind: "url", url: "https://x.com", title: "x" },
      ],
    });
    const got = extractArtifactsFromResult(r);
    expect(got).toHaveLength(3);
    expect(got.map((a: ToolArtifact) => a.kind)).toEqual(["file", "file", "url"]);
  });

  // 双层兜底：envelope 没 artifacts，但 inner result 有 — 仍提取
  it("falls through to inner result.artifacts when envelope lacks artifacts", () => {
    const inner = JSON.stringify({
      ok: true,
      artifacts: [{ kind: "image", path: "/img.png", title: "img" }],
    });
    const envelope = JSON.stringify({ ok: true, result: inner, error: null });
    const got = extractArtifactsFromResult(envelope);
    expect(got).toHaveLength(1);
    expect(got[0].kind).toBe("image");
  });

  // T5-5 守护：empty string → []，不 throw
  it("returns [] on empty string", () => {
    expect(extractArtifactsFromResult("")).toEqual([]);
  });
});
