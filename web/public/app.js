/* pergam.web — landing (upload) + view page glue.
   Vanilla JS, ~200 LOC, no build step. */

(() => {
  // ─── Config ─────────────────────────────────────────────────────
  // The Cloudflare Worker base URL. In production this is set to
  // https://api.pergam.dev. For local dev, override by setting
  // window.PERGAM_API in a <script> tag before this file.
  const API = (window.PERGAM_API || "https://api.pergam.dev").replace(/\/+$/, "");
  const MAX_BYTES = 128 * 1024;

  // ─── tiny DOM helpers ───────────────────────────────────────────
  const $ = (sel, root = document) => root.querySelector(sel);
  const fmtExpiry = (iso) => {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit"
    });
  };
  const fmtSize = (n) => n < 1024 ? `${n} B` : `${(n/1024).toFixed(1)} KB`;

  // ────────────────────────────────────────────────────────────────
  //  VIEW PAGE  (view.html?t=<token>  OR  /s/<token> via redirect)
  // ────────────────────────────────────────────────────────────────
  if (document.body.classList.contains("viewer-page")) {
    const params = new URLSearchParams(location.search);
    const token = params.get("t") || (location.pathname.match(/^\/s\/([a-z0-9]+)/i)?.[1] ?? "");
    const titleEl = $("#vb-title");
    const expiryEl = $("#vb-expiry");
    const frame = $("#viewer-frame");
    const empty = $("#viewer-empty");
    const dl = $("#vb-download");

    if (!/^[a-z0-9]{4,40}$/i.test(token)) {
      titleEl.textContent = "";
      expiryEl.textContent = "";
      frame.hidden = true;
      empty.hidden = false;
      return;
    }

    titleEl.textContent = "loading…";
    fetch(`${API}/s/${encodeURIComponent(token)}/meta`)
      .then(async (r) => {
        if (r.status === 404) throw new Error("notfound");
        if (r.status === 410) throw new Error("expired");
        if (!r.ok) throw new Error("err");
        return r.json();
      })
      .then((meta) => {
        document.title = `${meta.title || "Shared pergam"} · pergam`;
        titleEl.textContent = meta.title || "Untitled pergam";
        expiryEl.textContent = `expires ${fmtExpiry(meta.expires_at)} · ${fmtSize(meta.bytes)}`;
        dl.href = `${API}/s/${encodeURIComponent(token)}/raw`;
        dl.download = `${(meta.title || "pergam").replace(/[^a-z0-9-_]+/gi, "_")}.html`;
        dl.hidden = false;
        frame.src = `${API}/s/${encodeURIComponent(token)}`;
      })
      .catch((err) => {
        titleEl.textContent = "";
        expiryEl.textContent = "";
        frame.hidden = true;
        empty.hidden = false;
        $("#empty-title").textContent = err.message === "expired" ? "Expired" : "Not found";
        $("#empty-msg").textContent = err.message === "expired"
          ? "This share has reached its 72-hour TTL and was deleted."
          : "This link doesn't exist or has already expired.";
      });
    return;
  }

  // ────────────────────────────────────────────────────────────────
  //  LANDING — drag-drop upload
  // ────────────────────────────────────────────────────────────────
  const dropzone = $("#dropzone");
  if (!dropzone) return;

  const fileInput = $("#file-input");
  const progress = $("#dz-progress");
  const modal = $("#result-modal");
  const resultUrl = $("#result-url");
  const resultExpiry = $("#result-expiry");
  const resultOpen = $("#result-open");
  const resultCopy = $("#result-copy");

  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) handleFile(fileInput.files[0]);
    fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach(ev =>
    dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach(ev =>
    dropzone.addEventListener(ev, () => dropzone.classList.remove("drag")));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  });

  function handleFile(file) {
    if (file.size === 0) return toast("Empty file.");
    if (file.size > MAX_BYTES) return toast(`Too large: ${fmtSize(file.size)} (max ${fmtSize(MAX_BYTES)}).`);

    setUploading(true);
    const reader = new FileReader();
    reader.onload = async () => {
      const html = String(reader.result || "");
      const title = inferTitle(html) || file.name.replace(/\.html?$/i, "");
      try {
        const res = await fetch(`${API}/share`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ html, title, type: "otro", author: "anonymous" }),
        });
        if (res.status === 429) throw new Error("Rate limited. Try again in an hour.");
        if (res.status === 413) throw new Error("File too large.");
        if (!res.ok) throw new Error(`Upload failed (HTTP ${res.status})`);
        const data = await res.json();
        showResult(data);
      } catch (err) {
        toast(err.message || "Upload failed.");
      } finally {
        setUploading(false);
      }
    };
    reader.onerror = () => { toast("Couldn't read file."); setUploading(false); };
    reader.readAsText(file);
  }

  function setUploading(on) {
    dropzone.classList.toggle("uploading", on);
    progress.hidden = !on;
  }

  function inferTitle(html) {
    const m = html.match(/<title[^>]*>([^<]+)<\/title>/i);
    return m ? m[1].trim() : null;
  }

  function showResult(data) {
    const url = data.view_url || `${location.origin}/s/${data.token}`;
    resultUrl.value = url;
    resultOpen.href = url;
    resultExpiry.textContent = data.expires_at ? `Expires ${fmtExpiry(data.expires_at)}` : "";
    modal.hidden = false;
    // auto-copy
    navigator.clipboard?.writeText(url).then(
      () => { resultCopy.textContent = "Copied ✓"; },
      () => { resultCopy.textContent = "Copy"; }
    );
    resultUrl.select();
  }

  resultCopy?.addEventListener("click", () => {
    resultUrl.select();
    navigator.clipboard?.writeText(resultUrl.value).then(
      () => { resultCopy.textContent = "Copied ✓"; setTimeout(() => resultCopy.textContent = "Copy", 1500); }
    );
  });

  document.querySelectorAll("#result-modal .close").forEach(b =>
    b.addEventListener("click", () => { modal.hidden = true; resultCopy.textContent = "Copy"; }));

  // simple inline toast (uses the modal slot if open, otherwise alert)
  function toast(msg) {
    if (!modal.hidden) { /* shouldn't happen, fall through */ }
    alert(msg);
  }

  // ────────────────────────────────────────────────────────────────
  //  GitHub star count — only shown once we cross the threshold.
  //  Anything below that stays hidden to avoid "1 ⭐" embarrassment.
  // ────────────────────────────────────────────────────────────────
  const STAR_THRESHOLD = 9;
  const starSlots = document.querySelectorAll("[data-gh-stars]");
  if (starSlots.length) {
    fetch("https://api.github.com/repos/diesilveira/pergam", {
      headers: { Accept: "application/vnd.github+json" },
    })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((repo) => {
        const n = repo?.stargazers_count;
        if (typeof n === "number" && n >= STAR_THRESHOLD) {
          starSlots.forEach((el) => {
            el.textContent = String(n);
            el.hidden = false;
          });
        }
      })
      .catch(() => { /* silent: leave the slot hidden */ });
  }
})();
