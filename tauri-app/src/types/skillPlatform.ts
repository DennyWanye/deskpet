// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S20 skill-platform shared types — TypeScript mirror of
 * `backend/deskpet/types/skill_platform.py`.
 *
 * Keep in sync with the Python dataclasses; both define the wire
 * contract for control-WS IPC messages added by the skill platform.
 *
 * Refs:
 *   - openspec/changes/deskpet-skill-platform/specs/permission-gate/spec.md
 *   - openspec/changes/deskpet-skill-platform/specs/tool-use/spec.md
 */

/** 7 permission categories from the permission-gate spec. */
export type PermissionCategory =
  | "read_file"
  | "read_file_sensitive"
  | "write_file"
  | "desktop_write"
  | "shell"
  | "network"
  | "mcp_call"
  | "skill_install";

/** Tier of skill source — used for override priority + UI badges. */
export type SkillSourceTier = "bundled" | "user" | "project" | "plugin";

/**
 * Backend → frontend on the control WS. Render as a modal popup with
 * three buttons: "Yes once" / "Yes always for session" / "No".
 */
export interface PermissionRequest {
  type: "permission_request";
  payload: {
    request_id: string;
    category: PermissionCategory;
    summary: string;
    params: Record<string, unknown>;
    default_action: "allow" | "prompt" | "deny";
    dangerous: boolean;
    session_id: string;
  };
}

/** Frontend → backend reply. */
export interface PermissionResponse {
  type: "permission_response";
  payload: {
    request_id: string;
    decision: "allow" | "allow_session" | "deny";
  };
}

/** Backend -> frontend: ask the user for a clarification answer. */
export interface ClarificationRequest {
  type: "clarification_request";
  payload: {
    request_id: string;
    question: string;
    options?: string[];
  };
}

/** Frontend -> backend clarification reply. */
export interface ClarificationResponse {
  type: "clarification_response";
  payload: {
    request_id: string;
    answer: string;
  };
}

export interface PPTOutlineHistoryItem {
  outline_id: string;
  topic: string;
  created_at: string;
  sources_count: number;
  status: string;
}

/** Backend -> frontend: PPT Pro outline confirmation card. */
export interface PPTOutlineProposed {
  type: "ppt_outline_proposed";
  payload: {
    outline_id: string;
    topic: string;
    outline_md: string;
    session_id: string;
    sources_count: number;
    no_research: boolean;
    history: PPTOutlineHistoryItem[];
  };
}

/** Frontend -> backend: user's decision on a PPT Pro outline card. */
export interface PPTOutlineDecision {
  type: "ppt_outline_decision";
  payload: {
    outline_id: string;
    action: "accept" | "modify" | "cancel" | "reuse";
    feedback?: string;
    reuse_id?: string;
  };
}

/** Backend -> frontend broadcast: clear stale copies of an outline card. */
export interface PPTOutlineResolved {
  type: "ppt_outline_resolved";
  payload: {
    outline_id: string;
  };
}

/**
 * Streaming event during a tool_use turn. The chat panel uses these to
 * render inline tool steps ("📖 reading foo.txt…", "✅ done").
 */
export interface ToolUseEvent {
  type: "tool_use_event";
  payload: {
    kind: "request" | "result" | "cancelled";
    tool_name: string;
    params?: Record<string, unknown>;
    result?: unknown;
    error?: string | null;
    turn: number;
  };
}

/** SkillMeta returned by `skill_list_response`. */
export interface SkillMeta {
  name: string;
  description: string;
  when_to_use?: string;
  source: SkillSourceTier | string; // can be "plugin:<name>"
  disable_model_invocation?: boolean;
  user_invocable?: boolean;
  allowed_tools?: string[];
  paths?: string[];
  version?: string;
  path: string;
  overrides?: SkillSourceTier[];
}

/** Marketplace listing entry. */
export interface MarketplaceSkill {
  name: string;
  description: string;
  source_url: string;
  manifest_url?: string;
  author?: string;
  permission_categories?: PermissionCategory[];
}

/** Plugin manifest. */
export interface PluginManifest {
  name: string;
  version: string;
  description?: string;
  skills_dir?: string;
  mcp_servers_file?: string;
  tools_dir?: string;
  requires?: string[];
}
