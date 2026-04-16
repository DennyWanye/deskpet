/**
 * P2-1: push cloud config changes to a running backend.
 *
 * The secret is injected as X-Shared-Secret header so the renderer
 * never exposes it to the DOM or console.
 */

export interface CloudConfigUpdate {
  base_url: string;
  model: string;
  api_key?: string;   // empty/absent = keep current
  strategy?: string;  // empty/absent = keep current
}

export interface CloudConfigResult {
  ok: boolean;
  cloud_configured: boolean;
  base_url: string;
  model: string;
  has_api_key: boolean;
  strategy: string;
}

/**
 * POST /config/cloud to the running backend.
 * Throws on network error or non-200 status.
 */
export async function updateCloudConfig(
  secret: string,
  update: CloudConfigUpdate,
): Promise<CloudConfigResult> {
  const resp = await fetch("http://127.0.0.1:8100/config/cloud", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Shared-Secret": secret,
    },
    body: JSON.stringify(update),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`config/cloud failed (${resp.status}): ${text}`);
  }
  return resp.json();
}
