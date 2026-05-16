/**
 * pergam-share — Cloudflare Worker.
 *
 *   POST /share          → store HTML, return { token, view_url, expires_at }
 *   GET  /s/:token       → render HTML (text/html, strict CSP)
 *   GET  /s/:token/raw   → render HTML (text/plain, for download / debug)
 *   GET  /s/:token/meta  → { title, expires_at, bytes, … }
 *   GET  /healthz        → { ok: true }
 *
 * Storage: Cloudflare Workers KV with native TTL (72h).
 * Rate limits:
 *   - POST /share: 1 per 60s per IP (CF binding, no KV) +
 *                  5 per hour per IP (KV counter) +
 *                  100 per day total (KV counter, global cap)
 *   - GET  /s/*:   60 per 60s per IP on cache misses (CF binding)
 * Body size: 128 KB max.
 */

// Local type — the project's @cloudflare/workers-types is older than the
// RateLimit binding addition, so we declare the surface we use.
interface RateLimit {
  limit(opts: { key: string }): Promise<{ success: boolean }>;
}

export interface Env {
  KV: KVNamespace;
  RL_POST_COOLDOWN: RateLimit;
  RL_GET: RateLimit;
  ALLOWED_ORIGIN?: string;
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
}

const TTL_SECONDS         = 72 * 60 * 60;   // 72h
const RATE_WINDOW         = 60 * 60;        // 1h window for per-IP hourly counter
const RATE_POST_HOURLY    = 5;              // /share per hour per IP
const RATE_GLOBAL_DAILY   = 100;            // /share per UTC day, all IPs
const MAX_BYTES           = 128 * 1024;     // 128 KB — protects KV free tier write units
const CACHE_MAX_AGE       = 300;            // 5 min edge cache for GET /s/:token

// Prepended to every served pergam. Intercepts form submits and focus
// on password inputs, blocks them, and postMessages the parent
// (pergam.dev) so it can render an abuse-warning banner. Registers
// listeners in the capture phase on `document` BEFORE the user's HTML
// parses, so user scripts cannot out-race us (capture order is
// registration order, and we are first on the node).
//
// Defense-in-depth — `form-action 'none'` and `connect-src` already
// block the actual data exfil; this layer surfaces it to the viewer.
const DEFENSIVE_SCRIPT = `<script>
(function(){try{
var seen={form:false,pwd:false};
function tell(k){if(seen[k])return;seen[k]=true;try{parent.postMessage({type:'pergam:warn',kind:k},'*');}catch(_){}}
document.addEventListener('submit',function(e){e.preventDefault();tell('form');},true);
document.addEventListener('focusin',function(e){var t=e.target;if(t&&t.tagName==='INPUT'&&String(t.type||'').toLowerCase()==='password'){try{t.blur();}catch(_){}tell('pwd');}},true);
}catch(_){}})();
</script>`;

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
function json(data: unknown, status: number, env: Env, extra: Record<string, string> = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...cors(env), ...extra },
  });
}

// YYYYMMDD in UTC. Daily quota rolls over at 00:00 UTC.
function isoDay(): string {
  return new Date().toISOString().slice(0, 10).replace(/-/g, "");
}

// Per-IP hourly counter. Best-effort (read-modify-write, TOCTOU acceptable
// at our scale). Returns true if the request fits under the hourly cap.
async function hourlyHit(env: Env, ip: string): Promise<boolean> {
  const key = `rl:posth:${ip}`;
  const cur = parseInt((await env.KV.get(key)) || "0", 10);
  if (cur >= RATE_POST_HOURLY) return false;
  await env.KV.put(key, String(cur + 1), { expirationTtl: RATE_WINDOW });
  return true;
}

async function readGlobal(env: Env): Promise<number> {
  return parseInt((await env.KV.get(`g:share:${isoDay()}`)) || "0", 10);
}
async function bumpGlobal(env: Env): Promise<void> {
  const key = `g:share:${isoDay()}`;
  const cur = parseInt((await env.KV.get(key)) || "0", 10);
  // 48h TTL gives buffer around UTC-day rollover.
  await env.KV.put(key, String(cur + 1), { expirationTtl: 48 * 60 * 60 });
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const ip  = req.headers.get("cf-connecting-ip") || "unknown";

    // CORS preflight
    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors(env) });
    }

    if (url.pathname === "/healthz") {
      return json({ ok: true }, 200, env);
    }

    // ─── POST /share ─────────────────────────────────────────────
    if (req.method === "POST" && url.pathname === "/share") {
      // Origin gate: if the browser sends an Origin header, it must match
      // ALLOWED_ORIGIN. Server-to-server calls (no Origin) still pass —
      // this only filters casual web-to-web abuse from other pages.
      const origin = req.headers.get("Origin");
      if (origin && env.ALLOWED_ORIGIN && origin !== env.ALLOWED_ORIGIN) {
        return json({ error: "origin not allowed" }, 403, env);
      }

      const ct = req.headers.get("Content-Type") || "";
      if (!ct.toLowerCase().startsWith("application/json")) {
        return json({ error: "content-type must be application/json" }, 415, env);
      }

      // Defense-in-depth: reject before reading body if declared length
      // exceeds the cap. The post-parse byte count is the real gate.
      const lenStr = req.headers.get("Content-Length");
      if (!lenStr) {
        return json({ error: "content-length required" }, 411, env);
      }
      const declaredLen = parseInt(lenStr, 10);
      if (Number.isNaN(declaredLen) || declaredLen > MAX_BYTES) {
        return json({ error: "payload too large", limit_bytes: MAX_BYTES }, 413, env);
      }

      // 1) 60s cooldown per IP (CF binding — no KV touched).
      //    Stops repeat POSTs from the same IP before any other work.
      const cd = await env.RL_POST_COOLDOWN.limit({ key: ip });
      if (!cd.success) {
        return json(
          { error: "rate limit", retry_after_seconds: 60 },
          429, env, { "Retry-After": "60" },
        );
      }

      // 2) Global daily cap. Read-only at this point; we only increment
      //    after the share is successfully stored.
      if ((await readGlobal(env)) >= RATE_GLOBAL_DAILY) {
        return json({ error: "global daily cap reached, try tomorrow" }, 429, env);
      }

      // 3) Per-IP hourly cap.
      if (!(await hourlyHit(env, ip))) {
        return json(
          { error: "hourly rate limit", limit_per_hour: RATE_POST_HOURLY },
          429, env,
        );
      }

      let body: any;
      try {
        body = await req.json();
      } catch {
        return json({ error: "invalid JSON" }, 400, env);
      }

      const html        = String(body?.html || "");
      const title       = String(body?.title || "Shared pergam").slice(0, 200);
      const pergam_type = String(body?.type || "otro").slice(0, 40);
      const author      = String(body?.author || "anonymous").slice(0, 200);

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
      };
      await env.KV.put(`s:${token}`, JSON.stringify(record), { expirationTtl: TTL_SECONDS });
      await bumpGlobal(env);

      const publicHost = env.ALLOWED_ORIGIN || `https://${url.host}`;
      return json(
        { token, view_url: `${publicHost}/s/${token}`, expires_at: expiresAt, bytes },
        201,
        env
      );
    }

    // ─── GET /s/:token{,/raw,/meta} ──────────────────────────────
    // Tightened min length to 8 chars (our tokens are 10) — at 4 chars
    // the keyspace was ~900k which is brute-forceable.
    const m = url.pathname.match(/^\/s\/([a-z0-9]{8,40})(\/raw|\/meta)?$/);
    if (req.method === "GET" && m) {
      const [, token, sub] = m;

      // Cache: try the edge cache first. Hits return immediately without
      // touching the rate limiter or KV. /raw is skipped because the
      // Content-Disposition filename is per-record.
      const cache = caches.default;
      if (sub !== "/raw") {
        const cached = await cache.match(req);
        if (cached) return cached;
      }

      // Rate limit on cache miss only. Hot pergams (viral links) burn
      // ~zero rate budget because the edge serves them.
      const rl = await env.RL_GET.limit({ key: ip });
      if (!rl.success) {
        return json(
          { error: "rate limit", retry_after_seconds: 60 },
          429, env, { "Retry-After": "60" },
        );
      }

      const raw = await env.KV.get(`s:${token}`);
      if (!raw) return json({ error: "not found or expired" }, 404, env);
      const rec = JSON.parse(raw) as ShareRecord;

      let response: Response;

      if (sub === "/meta") {
        response = new Response(
          JSON.stringify({
            title: rec.title,
            type: rec.type,
            author: rec.author,
            bytes: rec.bytes,
            created_at: rec.created_at,
            expires_at: rec.expires_at,
          }),
          {
            status: 200,
            headers: {
              "Content-Type": "application/json; charset=utf-8",
              "Cache-Control": `public, max-age=${CACHE_MAX_AGE}`,
              ...cors(env),
            },
          },
        );
      } else if (sub === "/raw") {
        return new Response(rec.html, {
          status: 200,
          headers: {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": `inline; filename="${rec.title.replace(/[^a-z0-9_-]+/gi, "_")}.html"`,
            ...cors(env),
          },
        });
      } else {
        // /s/:token → render HTML. Strict CSP: inline CSS + Mermaid CDN only.
        // Intentionally no X-Frame-Options here — CSP's frame-ancestors
        // allowlist (pergam.dev) is the gate; XFO would override it and
        // block legitimate embedding.
        //
        // `form-action` does NOT inherit from `default-src` (per CSP3),
        // so we name it explicitly to keep any phishing form from
        // POSTing data anywhere. `img-src` is intentionally tight to
        // `'self' data:` to close the `<img src=evil/?q=stolen>` pixel
        // exfil channel — content authors lose the ability to hotlink
        // remote images, but that's an acceptable tradeoff.
        // frame-ancestors mirrors ALLOWED_ORIGIN so a dev environment
        // can iframe the worker from http://localhost:8080 (or wherever
        // the local landing is served) without code changes. Falls back
        // to the production origin so a misconfigured env still safe.
        const frameOrigin = env.ALLOWED_ORIGIN || "https://pergam.dev";
        const csp = [
          "default-src 'none'",
          "style-src 'unsafe-inline' https://cdn.jsdelivr.net",
          "script-src 'unsafe-inline' https://cdn.jsdelivr.net",
          "img-src 'self' data:",
          "font-src data: https://cdn.jsdelivr.net",
          "connect-src https://cdn.jsdelivr.net",
          "form-action 'none'",
          `frame-ancestors 'self' ${frameOrigin}`,
          "base-uri 'none'",
        ].join("; ");

        response = new Response(DEFENSIVE_SCRIPT + rec.html, {
          status: 200,
          headers: {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Security-Policy": csp,
            "Referrer-Policy": "no-referrer",
            "Cache-Control": `public, max-age=${CACHE_MAX_AGE}`,
            ...cors(env),
          },
        });
      }

      // Store in the edge cache. clone() because the body stream can
      // only be consumed once.
      await cache.put(req, response.clone());
      return response;
    }

    return json({ error: "not found" }, 404, env);
  },
};
