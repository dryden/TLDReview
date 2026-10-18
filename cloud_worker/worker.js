/**
 * TL;DReview Zero-Knowledge E2EE Cloud Vault Relay (Cloudflare Worker + KV)
 *
 * Endpoints:
 *   - GET /v1/vault/:vault_id
 *       Returns the encrypted JSON envelope `{ v, nonce, ct, tag, updated_at }`
 *       or 404 if not found.
 *   - PUT /v1/vault/:vault_id
 *       Requires `Authorization: Bearer <write_token>`.
 *       Uses Trust-On-First-Use (TOFU): stores `SHA-256(write_token)` in KV metadata
 *       on first write, and verifies constant-time equality on subsequent updates.
 *       Enforces a strict 512 KB payload size limit and strict JSON envelope schema.
 *
 * Required KV Namespace Binding in wrangler.toml:
 *   - VAULT_KV
 */

const MAX_PAYLOAD_BYTES = 512 * 1024; // 512 KB cap
const VAULT_ID_REGEX = /^[A-Za-z0-9_-]{12,32}$/;
const RATE_LIMIT_WINDOW_SEC = 60;
const RATE_LIMIT_MAX_WRITES = 30;

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, PUT, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

function jsonResponse(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...CORS_HEADERS,
      ...extraHeaders,
    },
  });
}

async function sha256Hex(input) {
  const bytes = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function timingSafeEqualHex(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) {
    return false;
  }
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

function extractBearerToken(request) {
  const auth = request.headers.get("Authorization") || "";
  if (!auth.startsWith("Bearer ")) {
    return null;
  }
  const token = auth.slice(7).trim();
  return token.length >= 32 ? token : null;
}

function validateEnvelope(obj) {
  if (!obj || typeof obj !== "object") return "Payload must be a JSON object.";
  if (obj.v !== 1) return "Unsupported envelope version (expected v=1).";
  if (typeof obj.nonce !== "string" || obj.nonce.length < 16 || obj.nonce.length > 64) {
    return "Invalid or missing 'nonce' field.";
  }
  if (typeof obj.ct !== "string" || obj.ct.length < 4) {
    return "Invalid or missing 'ct' (ciphertext) field.";
  }
  if (typeof obj.tag !== "string" || obj.tag.length < 32 || obj.tag.length > 128) {
    return "Invalid or missing 'tag' (HMAC) field.";
  }
  if (typeof obj.updated_at !== "number") {
    return "Invalid or missing numeric 'updated_at' timestamp.";
  }
  return null;
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    const url = new URL(request.url);
    if (request.method === "GET" && (url.pathname === "/health" || url.pathname === "/v1/vault/health")) {
      return jsonResponse({
        status: "ok",
        service: "tldreview-vault-relay",
        kv_bound: Boolean(env.VAULT_KV),
      }, 200);
    }

    const match = url.pathname.match(/^\/v1\/vault\/([A-Za-z0-9_-]+)$/);
    if (!match) {
      return jsonResponse({ status: "error", message: "Not Found" }, 404);
    }

    const vaultId = match[1];
    if (!VAULT_ID_REGEX.test(vaultId)) {
      return jsonResponse(
        { status: "error", message: "Invalid vault_id format (must be 12-32 URL-safe chars)." },
        400
      );
    }

    if (!env.VAULT_KV) {
      return jsonResponse({ status: "error", message: "VAULT_KV binding is not configured." }, 500);
    }

    const kvKey = `vault:${vaultId}`;

    // GET /v1/vault/:vault_id
    if (request.method === "GET") {
      const stored = await env.VAULT_KV.get(kvKey, { type: "text" });
      if (!stored) {
        return jsonResponse({ status: "error", message: "Vault not found." }, 404);
      }
      return new Response(stored, {
        status: 200,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "no-store",
          ...CORS_HEADERS,
        },
      });
    }

    // PUT /v1/vault/:vault_id
    if (request.method === "PUT") {
      const writeToken = extractBearerToken(request);
      if (!writeToken) {
        return jsonResponse(
          { status: "error", message: "Missing or invalid Authorization: Bearer <write_token> header." },
          401
        );
      }

      const contentLength = Number(request.headers.get("Content-Length") || "0");
      if (contentLength > MAX_PAYLOAD_BYTES) {
        return jsonResponse({ status: "error", message: "Payload exceeds 512 KB limit." }, 413);
      }

      const rawBody = await request.text();
      if (new TextEncoder().encode(rawBody).byteLength > MAX_PAYLOAD_BYTES) {
        return jsonResponse({ status: "error", message: "Payload exceeds 512 KB limit." }, 413);
      }

      let envelope;
      try {
        envelope = JSON.parse(rawBody);
      } catch {
        return jsonResponse({ status: "error", message: "Malformed JSON body." }, 400);
      }

      const validationErr = validateEnvelope(envelope);
      if (validationErr) {
        return jsonResponse({ status: "error", message: validationErr }, 400);
      }

      // Per-IP + vault_id lightweight rate limit check via KV if needed
      const clientIp = request.headers.get("CF-Connecting-IP") || "anon";
      const rlKey = `rl:${vaultId}:${clientIp}:${Math.floor(Date.now() / (RATE_LIMIT_WINDOW_SEC * 1000))}`;
      const currentHits = Number((await env.VAULT_KV.get(rlKey)) || "0");
      if (currentHits >= RATE_LIMIT_MAX_WRITES) {
        return jsonResponse({ status: "error", message: "Rate limit exceeded. Try again in a minute." }, 429);
      }

      const incomingTokenHash = await sha256Hex(writeToken);
      const existing = await env.VAULT_KV.getWithMetadata(kvKey, { type: "text" });

      if (existing && existing.value !== null) {
        const storedHash = existing.metadata && existing.metadata.token_sha256;
        if (!storedHash || !timingSafeEqualHex(storedHash, incomingTokenHash)) {
          return jsonResponse({ status: "error", message: "Forbidden: invalid write_token for this vault." }, 403);
        }
      }

      const normalizedEnvelope = JSON.stringify({
        v: 1,
        nonce: envelope.nonce,
        ct: envelope.ct,
        tag: envelope.tag,
        updated_at: envelope.updated_at,
      });

      await env.VAULT_KV.put(kvKey, normalizedEnvelope, {
        metadata: {
          token_sha256: incomingTokenHash,
          updated_at: envelope.updated_at,
        },
      });
      await env.VAULT_KV.put(rlKey, String(currentHits + 1), {
        expirationTtl: RATE_LIMIT_WINDOW_SEC,
      });

      return jsonResponse({
        status: "ok",
        vault_id: vaultId,
        updated_at: envelope.updated_at,
      });
    }

    return jsonResponse({ status: "error", message: "Method Not Allowed" }, 405);
  },
};
