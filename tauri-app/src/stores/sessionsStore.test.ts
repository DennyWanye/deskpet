/**
 * P5-S1 D — severity_score formula + pet_focus_sid selector tests.
 */
import { describe, expect, it } from "vitest";

import {
  type SessionState,
  pet_focus_sid,
  severity_score,
  severity_score_breakdown,
} from "./sessionsStore";

function mk(over: Partial<SessionState>): SessionState {
  return {
    base_session_id: "sid",
    code_session_id: "code-x",
    project_root: "/tmp/x",
    project_name: "x",
    messages: [],
    todos: [],
    token_usage: { prompt: 0, completion: 0 },
    status: "running",
    last_activity: Date.now(),
    inflight: false,
    current_iteration: 0,
    max_iterations: 50,
    tool_signature_repeat: 0,
    supervisor_severity: "green",
    supervisor_alert: null,
    ...over,
  };
}

describe("severity_score_breakdown", () => {
  it("idle session with fresh activity scores 0", () => {
    const s = mk({ status: "idle" });
    const b = severity_score_breakdown(s, Date.now());
    expect(b.total).toBe(0);
  });

  it("running session with no other signals scores 10 (base)", () => {
    const s = mk({ status: "running" });
    const b = severity_score_breakdown(s, Date.now());
    expect(b.base).toBe(10);
    expect(b.total).toBe(10);
  });

  it("permission status scores higher than running", () => {
    const s = mk({ status: "permission" });
    const b = severity_score_breakdown(s, Date.now());
    expect(b.base).toBe(25);
  });

  it("error status scores 60", () => {
    const s = mk({ status: "error" });
    const b = severity_score_breakdown(s, Date.now());
    expect(b.base).toBe(60);
  });

  it("yellow supervisor adds 20 boost", () => {
    const s = mk({ supervisor_severity: "yellow" });
    const b = severity_score_breakdown(s);
    expect(b.supervisor).toBe(20);
  });

  it("red supervisor adds 50 boost", () => {
    const s = mk({ supervisor_severity: "red" });
    const b = severity_score_breakdown(s);
    expect(b.supervisor).toBe(50);
  });

  it("repeat penalty caps at 40", () => {
    const s = mk({ tool_signature_repeat: 100 });
    const b = severity_score_breakdown(s);
    expect(b.repeat).toBe(40);
  });

  it("repeat penalty: 4 repeats = 40 points", () => {
    const s = mk({ tool_signature_repeat: 4 });
    const b = severity_score_breakdown(s);
    expect(b.repeat).toBe(40);
  });

  it("age penalty grows logarithmically with inactivity", () => {
    const now = Date.now();
    const fresh = severity_score_breakdown(mk({ last_activity: now }), now);
    const oneMin = severity_score_breakdown(
      mk({ last_activity: now - 60_000 }),
      now,
    );
    const sixteenMin = severity_score_breakdown(
      mk({ last_activity: now - 16 * 60_000 }),
      now,
    );
    expect(fresh.age).toBe(0);
    expect(oneMin.age).toBe(0); // log2(1) * 6 = 0
    expect(sixteenMin.age).toBeCloseTo(24, 0); // log2(16) * 6 = 24
  });

  it("age penalty caps at 30", () => {
    const now = Date.now();
    const veryOld = severity_score_breakdown(
      mk({ last_activity: now - 1000 * 60 * 1000 }),
      now,
    );
    expect(veryOld.age).toBe(30);
  });

  it("iteration pressure: 25/50 iters = 5 points", () => {
    const s = mk({ current_iteration: 25, max_iterations: 50 });
    const b = severity_score_breakdown(s);
    expect(b.iteration).toBe(5);
  });

  it("worst-case (error + repeat 8 + red + iter 50/50) sums above 160", () => {
    const s = mk({
      status: "error",
      tool_signature_repeat: 8,
      supervisor_severity: "red",
      current_iteration: 50,
      max_iterations: 50,
    });
    const total = severity_score(s);
    // 60 + 0 + 40 + 50 + 10 = 160 (age 0 since fresh)
    expect(total).toBe(160);
  });
});

describe("pet_focus_sid", () => {
  const now = Date.now();

  it("returns null when no eligible sessions", () => {
    expect(pet_focus_sid({}, now)).toBeNull();
    // Companion default has no project_root, not eligible
    expect(
      pet_focus_sid(
        {
          default: mk({
            base_session_id: "default",
            code_session_id: null,
            project_root: null,
          }),
        },
        now,
      ),
    ).toBeNull();
  });

  it("picks the highest-scoring sid", () => {
    const sids = {
      a: mk({ base_session_id: "a", status: "idle", supervisor_severity: "green" }),
      b: mk({ base_session_id: "b", status: "running", supervisor_severity: "yellow" }),
      c: mk({ base_session_id: "c", status: "running", tool_signature_repeat: 5 }),
    };
    expect(pet_focus_sid(sids, now)).toBe("c"); // 10 + 40 = 50, beats b's 30
  });

  it("companion-mode sids without project_root or code_session_id are excluded", () => {
    const sids = {
      default: mk({
        base_session_id: "default",
        code_session_id: null,
        project_root: null,
        status: "error", // would score 60 if eligible
      }),
      "code-real": mk({
        base_session_id: "code-real",
        status: "running", // 10
      }),
    };
    expect(pet_focus_sid(sids, now)).toBe("code-real");
  });
});
