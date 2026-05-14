/**
 * pergam-share — Cloudflare Worker.
 *
 *   POST /share         → store HTML, return { token, view_url, expires_at }
 *   GET  /s/:token       → render HTML (text/html, strict CSP)
 *   GET  /s/:token/raw   → render HTML (text/plain, for download / debug)
 *   GET  /s/:token/meta  → { title, expires_at, bytes, views }
 *   GET  /healthz        → { ok: true }
 *
 * Storage: Cloudflare Workers KV with native TTL (72h).
 * Rate limit: per-IP counters in KV with 1h TTL.
 * Body size: 128 KB max.
 */

export interface Env {
  KV: KVNamespace;
  ALLOWED_ORIGIN?: string; // e.g. https://pergam.dev
}

interface ShareRecord {
  html: string;
  title: string;
  type: string;
  author: string;
  bytes: number;
  created_at: string;
  expires_at: string;
  ip: string;
  views: number;
}

const TTL_SECONDS = 72 * 60 * 60;          // 72h
const RATE_WINDOW = 60 * 60;               // 1h
const RATE_POST   = 10;                    // /share per hour per IP
const RATE_GET    = 240;                   // /s/* per hour per IP
const MAX_BYTES   = 128 * 1024;            // 128 KB — protects KV free tier write units

const ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"; // no 0/1/l/i/o
function newToken(len = 10): string {
  const bytes = new Uint8Array(len);
  crypto.getRandomValues(bytes);
  let out = "";
  for (let i = 0; i < len; i++) out += ALPHABET[bytes[i] % ALPHABET.length];
  return out;
}

function cors(env: Env): Record<string, string> {
  const origin = env.ALLOWED_ORIGIN || "*";
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}
function json(data: unknown, status = 200, env: Env, extra: Record<string, string> = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...cors(env), ...extra },
  });
}

async function rateCheck(env: Env, ip: string, kind: "post" | "get"): Promise<boolean> {
  const key = `rl:${kind}:${ip}`;
  const cur = parseInt((await env.KV.get(key)) || "0", 10);
  const limit = kind === "post" ? RATE_POST : RATE_GET;
  if (cur >= limit) return false;
  await env.KV.put(key, String(cur + 1), { expirationTtl: RATE_WINDOW });
  return true;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const ip = req.headers.get("cf-connecting-ip") || "unknown";

    // CORS preflight
    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors(env) });
    }

    if (url.pathname === "/healthz") {
      return json({ ok: true }, 200, env);
    }

    // ─── POST /share ─────────────────────────────────────────────
    if (req.method === "POST" && url.pathname === "/share") {
      const lenStr = req.headers.get("Content-Length");
      if (lenStr && parseInt(lenStr, 10) > MAX_BYTES) {
        return json({ error: "payload too large", limit_bytes: MAX_BYTES }, 413, env);
      }
      if (!(await rateCheck(env, ip, "post"))) {
        return json({ error: "rate limit", limit_per_hour: RATE_POST }, 429, env);
      }

      let body: any;
      try {
        body = await req.json();
      } catch {
        return json({ error: "invalid JSON" }, 400, env);
      }

      const html = String(body?.html || "");
      const title = String(body?.title || "Shared pergam").slice(0, 200);
      const pergam_type = String(body?.type || "otro").slice(0, 40);
      const author = String(body?.author || "anonymous").slice(0, 200);

      if (!html.trim()) {
        return json({ error: "missing html" }, 400, env);
      }
      const bytes = new TextEncoder().encode(html).length;
      if (bytes > MAX_BYTES) {
        return json({ error: "payload too large", limit_bytes: MAX_BYTES, bytes }, 413, env);
      }

      // pick a token; collision is astronomically unlikely but check once
      let token = newToken();
      if (await env.KV.get(`s:${token}`)) token = newToken(12);

      const now = Date.now();
      const expiresAt = new Date(now + TTL_SECONDS * 1000).toISOString();
      const record: ShareRecord = {
        html,
        title,
        type: pergam_type,
        author,
        bytes,
        created_at: new Date(now).toISOString(),
        expires_at: expiresAt,
        ip,
        views: 0,
      };
      await env.KV.put(`s:${token}`, JSON.stringify(record), { expirationTtl: TTL_SECONDS });

      const publicHost = env.ALLOWED_ORIGIN || `https://${url.host}`;
      return json(
        { token, view_url: `${publicHost}/s/${token}`, expires_at: expiresAt, bytes },
        201,
        env
      );
    }

    // ─── GET /s/:token{,/raw,/meta} ──────────────────────────────
    const m = url.pathname.match(/^\/s\/([a-z0-9]{4,40})(\/raw|\/meta)?$/);
    if (req.method === "GET" && m) {
      const [, token, sub] = m;

      if (!(await rateCheck(env, ip, "get"))) {
        return json({ error: "rate limit", limit_per_hour: RATE_GET }, 429, env);
      }

      const raw = await env.KV.get(`s:${token}`);
      if (!raw) return json({ error: "not found or expired" }, 404, env);
      const rec = JSON.parse(raw) as ShareRecord;

      if (sub === "/meta") {
        return json(
          {
            title: rec.title,
            type: rec.type,
            author: rec.author,
            bytes: rec.bytes,
            created_at: rec.created_at,
            expires_at: rec.expires_at,
            views: rec.views,
          },
          200,
          env
        );
      }

      // increment views (fire-and-forget; not a hot path)
      rec.views = (rec.views || 0) + 1;
      // recompute TTL so we don't extend it accidentally
      const remaining = Math.max(
        60,
        Math.floor((new Date(rec.expires_at).getTime() - Date.now()) / 1000)
      );
      await env.KV.put(`s:${token}`, JSON.stringify(rec), { expirationTtl: remaining });

      if (sub === "/raw") {
        return new Response(rec.html, {
          status: 200,
          headers: {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": `inline; filename="${rec.title.replace(/[^a-z0-9_-]+/gi, "_")}.html"`,
            ...cors(env),
          },
        });
      }

      // /s/:token → render. Strict CSP: inline CSS + Mermaid CDN only.
      const csp = [
        "default-src 'none'",
        "style-src 'unsafe-inline' https://cdn.jsdelivr.net",
        "script-src 'unsafe-inline' https://cdn.jsdelivr.net",
        "img-src 'self' data: https:",
        "font-src data: https://cdn.jsdelivr.net",
        "connect-src https://cdn.jsdelivr.net",
        "frame-ancestors 'self' https://pergam.dev",
        "base-uri 'none'",
      ].join("; ");

      // Note: intentionally no X-Frame-Options header here. CSP's
      // frame-ancestors allows pergam.dev to embed this in an iframe;
      // adding X-Frame-Options: SAMEORIGIN would override that and
      // break the viewer (pergam.dev embedding api.pergam.dev content).
      return new Response(rec.html, {
        status: 200,
        headers: {
          "Content-Type": "text/html; charset=utf-8",
          "Content-Security-Policy": csp,
          "Referrer-Policy": "no-referrer",
          "Cache-Control": "private, max-age=300",
          ...cors(env),
        },
      });
    }

    return json({ error: "not found" }, 404, env);
  },
};
