#!/usr/bin/env node
/**
 * Local dev server for pergam.dev's static landing.
 *
 * Serves `web/public/` on http://localhost:8080 and:
 *   - Rewrites HTML to inject `window.PERGAM_API` before app.js loads,
 *     pointing at the local Worker (wrangler dev → http://localhost:8787).
 *   - Emulates the Cloudflare Pages `_redirects` rule
 *     `/s/:token  →  /view.html?t=:token`.
 *
 * Pair with the Worker in another terminal:
 *   cd web/worker && npx wrangler dev   # http://localhost:8787
 *
 * Then open http://localhost:8080
 *
 * Override the API endpoint with PERGAM_API=… if needed.
 */

const http = require("http");
const fs   = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..", "public");
const PORT = parseInt(process.env.PORT || "8080", 10);
const API  = process.env.PERGAM_API || "http://localhost:8787";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js":   "application/javascript; charset=utf-8",
  ".css":  "text/css; charset=utf-8",
  ".svg":  "image/svg+xml",
  ".png":  "image/png",
  ".ico":  "image/x-icon",
  ".json": "application/json",
};

http.createServer((req, res) => {
  let url = decodeURIComponent(req.url.split("?")[0]);

  // Mirror Cloudflare Pages `_redirects` rule:
  //   /s/:token  →  /view.html?t=:token   (status 200 = internal rewrite,
  // not redirect, so the URL bar stays at /s/:token). Reading the file
  // from the rewritten path while keeping `url` unchanged would also
  // work; we only need view.html's bytes here, the `t` param is read
  // from `location.search` by app.js which we don't touch.
  const m = url.match(/^\/s\/([a-z0-9]{4,40})$/i);
  let servePath = url;
  if (m) {
    servePath = "/view.html";
  }

  if (servePath === "/") servePath = "/index.html";

  const file = path.normalize(path.join(ROOT, servePath));
  if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    res.writeHead(404, { "Content-Type": "text/plain" });
    return res.end("404 — not found");
  }

  const ext = path.extname(file).toLowerCase();
  let body = fs.readFileSync(file);

  // Inject the API override into HTML right before app.js loads.
  // The escape on API guards against quote-injection if the env var
  // ever contains a single quote (shouldn't, but cheap to be safe).
  if (ext === ".html") {
    const safe = API.replace(/'/g, "\\'");
    const inject = `<script>window.PERGAM_API='${safe}';</script>\n`;
    body = body
      .toString("utf8")
      .replace(/(<script\s+src=["']?\/?app\.js["']?[^>]*><\/script>)/i, `${inject}$1`);
  }

  res.writeHead(200, {
    "Content-Type":  MIME[ext] || "application/octet-stream",
    "Cache-Control": "no-store",
  });
  res.end(body);
}).listen(PORT, () => {
  console.log(`pergam landing dev:   http://localhost:${PORT}`);
  console.log(`pergam API target:    ${API}`);
});
