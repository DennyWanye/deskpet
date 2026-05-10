/**
 * P5-S3 — Pet visual state machine.
 *
 * Maps a numeric severity_score (computed in sessionsStore) into one of
 * 5 visual states (idle / working / worried / alert / intervening) with
 * hysteresis and minimum dwell time so the pet doesn't "twitch" on
 * scores that hover near a boundary.
 *
 * Hiyori has no Expression resources, only Idle motions (m01..m10) plus
 * one TapBody. We encode the per-state Live2D parameter table here so
 * the App layer can call the imperative Live2D handle without knowing
 * about score thresholds.
 */
import type { SessionState } from "../stores/sessionsStore";
import {
  severity_score,
  severity_score_breakdown,
  pet_focus_sid,
} from "../stores/sessionsStore";

// ───────── public types ────────────────────────────────────────────

export type PetState =
  | "idle"
  | "working"
  | "worried"
  | "alert"
  | "intervening";

export interface PetMotionConfig {
  /** Pool of Idle motion ids to randomly cycle through; empty means
   * "use Live2D's default Idle group". */
  motion_pool: string[];
  /** Period (seconds) between forced motion switches; jitter is +/- 30%. */
  switch_period_seconds: number;
  /** Eye-blink frequency in Hz overlaid on the base motion. */
  blink_hz: number;
  /** Head tilt angle in degrees (positive = up, negative = down). */
  head_tilt: number;
  /** Trigger TapBody once when entering this state. */
  tap_on_entry: boolean;
}

export const STATE_CONFIG: Record<PetState, PetMotionConfig> = {
  idle: {
    motion_pool: [], // default Idle behaviour
    switch_period_seconds: 30,
    blink_hz: 0.2,
    head_tilt: 0,
    tap_on_entry: false,
  },
  working: {
    // Idle pool subset perceived as "perky"; calibration spike pending.
    motion_pool: ["Idle"], // play index 1 (m02) when Live2D supports it
    switch_period_seconds: 15,
    blink_hz: 0.3,
    head_tilt: 2,
    tap_on_entry: false,
  },
  worried: {
    motion_pool: ["Idle"], // slower subset
    switch_period_seconds: 45,
    blink_hz: 0.5,
    head_tilt: -5,
    tap_on_entry: false,
  },
  alert: {
    motion_pool: ["Idle"],
    switch_period_seconds: 60,
    blink_hz: 0.6,
    head_tilt: -8,
    tap_on_entry: true,
  },
  intervening: {
    motion_pool: ["Idle"],
    switch_period_seconds: 20,
    blink_hz: 0.3,
    head_tilt: 3,
    tap_on_entry: true,
  },
};

// ───────── state machine ───────────────────────────────────────────

const ENTER_WORRIED = 60;
const EXIT_WORRIED = 50;
const ENTER_ALERT = 100;
const EXIT_ALERT = 90;
const ENTER_WORKING = 30; // any session running
const EXIT_WORKING = 25;

const MIN_DWELL_MS = 10_000; // 10 seconds
const INTERVENING_DURATION_MS = 3_000;

export interface PetStateMachineOptions {
  /** Used by tests; defaults to Date.now(). */
  clock?: () => number;
}

export interface PetTickInput {
  sessions: Record<string, SessionState>;
  /** Set true when supervisor_alert with action=nudge just fired so the
   * machine briefly enters `intervening` overlay. */
  nudge_pulse?: boolean;
}

export interface PetTickResult {
  state: PetState;
  focus_sid: string | null;
  focus_score: number;
  focus_breakdown: ReturnType<typeof severity_score_breakdown> | null;
  motion: PetMotionConfig;
  state_changed: boolean;
}

/**
 * State-holding object. Construct once in App.tsx, call `tick()` on
 * each render or on relevant store updates.
 */
export class PetStateMachine {
  private current_state: PetState = "idle";
  private last_transition_ms: number;
  private intervening_until: number = 0;
  private now: () => number;

  constructor(opts: PetStateMachineOptions = {}) {
    this.now = opts.clock ?? (() => Date.now());
    this.last_transition_ms = this.now();
  }

  get state(): PetState {
    return this.current_state;
  }

  reset(): void {
    this.current_state = "idle";
    this.last_transition_ms = this.now();
    this.intervening_until = 0;
  }

  /**
   * Advance the state machine using current session state.
   * @returns the (possibly new) state, the focus sid, and the motion config.
   */
  tick(input: PetTickInput): PetTickResult {
    const now = this.now();
    const focus_sid = pet_focus_sid(input.sessions, now);
    const focus = focus_sid ? input.sessions[focus_sid] : null;
    const focus_score = focus ? severity_score(focus, now) : 0;
    const focus_breakdown = focus
      ? severity_score_breakdown(focus, now)
      : null;

    // Intervening overlay takes priority for its short duration. It
    // doesn't count as a state change for dwell-time purposes — the
    // underlying score-derived state still ticks under it.
    if (input.nudge_pulse) {
      this.intervening_until = now + INTERVENING_DURATION_MS;
    }

    let next_state: PetState = this.compute_state_from_score(focus_score, !!focus);

    // Apply minimum dwell time: don't change state for at least MIN_DWELL_MS.
    const dwell_age = now - this.last_transition_ms;
    if (next_state !== this.current_state && dwell_age < MIN_DWELL_MS) {
      next_state = this.current_state;
    }

    let state_changed = false;
    if (next_state !== this.current_state) {
      this.current_state = next_state;
      this.last_transition_ms = now;
      state_changed = true;
    }

    // Apply intervening overlay last so it doesn't perturb dwell book-keeping.
    let visible_state = this.current_state;
    if (now < this.intervening_until) {
      visible_state = "intervening";
    }

    return {
      state: visible_state,
      focus_sid,
      focus_score,
      focus_breakdown,
      motion: STATE_CONFIG[visible_state],
      state_changed,
    };
  }

  /**
   * Pure helper — given a score and whether any focus session exists,
   * return the score-derived state with hysteresis around current state.
   */
  private compute_state_from_score(
    score: number,
    has_focus: boolean,
  ): PetState {
    if (!has_focus) return "idle";

    switch (this.current_state) {
      case "alert":
        return score >= EXIT_ALERT ? "alert" : (score >= ENTER_WORRIED ? "worried" : (score >= ENTER_WORKING ? "working" : "idle"));
      case "worried":
        if (score >= ENTER_ALERT) return "alert";
        return score >= EXIT_WORRIED ? "worried" : (score >= ENTER_WORKING ? "working" : "idle");
      case "working":
        if (score >= ENTER_ALERT) return "alert";
        if (score >= ENTER_WORRIED) return "worried";
        return score >= EXIT_WORKING ? "working" : "idle";
      case "intervening":
        // intervening is overlay-driven; underlying state is whatever the
        // score says.
        if (score >= ENTER_ALERT) return "alert";
        if (score >= ENTER_WORRIED) return "worried";
        if (score >= ENTER_WORKING) return "working";
        return "idle";
      case "idle":
      default:
        if (score >= ENTER_ALERT) return "alert";
        if (score >= ENTER_WORRIED) return "worried";
        if (score >= ENTER_WORKING) return "working";
        return "idle";
    }
  }
}

// Exported constants for unit tests.
export const _internals = {
  ENTER_WORRIED,
  EXIT_WORRIED,
  ENTER_ALERT,
  EXIT_ALERT,
  ENTER_WORKING,
  EXIT_WORKING,
  MIN_DWELL_MS,
  INTERVENING_DURATION_MS,
};
