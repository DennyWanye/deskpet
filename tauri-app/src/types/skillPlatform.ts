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
