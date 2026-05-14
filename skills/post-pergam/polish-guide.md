# Polish guide for pergam HTML

Concrete techniques that turn a "functional" pergam into one that's pleasant to read. Use whichever apply — not every HTML needs all of these.

## When to reach for the full template

Use `template.html` (sibling file) as a starting skeleton when:

- The content is a **spec, design doc, or long-form report** (not a small dashboard).
- There are 5+ logical sections that benefit from a TOC.
- You have **diagrams** (flows, architecture, state machines) — Mermaid renders them better than ASCII art.
- You'll show **code or schemas** that benefit from manual syntax color.

For small comparison pergams (3–6 PR cards, leaderboard, simple status board), keep the existing card-layout style — the template is overkill.

## Layout

### Three-band vertical structure

```
┌──────────────────────────────────────────────┐
│  HEADER (crumbs, h1, sub, meta pills)        │
├──────────────────────────────────────────────┤
│  STATS row  (optional: counters)             │
├──────────────────────────────────────────────┤
│  MAIN content        │  STICKY TOC (right)   │
│  ...                 │  01. Section A        │
│  ...                 │  02. Section B        │
├──────────────────────────────────────────────┤
│  FOOTER                                      │
└──────────────────────────────────────────────┘
```

- TOC goes on the **right** (less visually heavy than left; main content reads natural width on the left).
- TOC is sticky with `position: sticky; top: 1rem;` and `max-height: calc(100vh - 2rem); overflow-y: auto;` so long TOCs scroll inside themselves.
- Each section has `scroll-margin-top: 1rem` so anchor jumps don't slam against the top edge.
- Use `html { scroll-behavior: smooth; }` so anchor clicks ease in.

### Content width

Cap the layout at ~1280px (`--content-max`). Anything wider hurts reading on big monitors. Keep the `<main>` column narrower than that minus the TOC.

### Responsive

When the viewport drops below ~980px, collapse to a single column and **move the TOC above the content** (`order: -1` on the aside). Below ~600px, drop horizontal padding.

## Diagrams — use Mermaid

Replace ASCII-art `<pre>` diagrams with Mermaid `<pre class="mermaid">` blocks. Use:

```html
<div class="mermaid-wrap">
  <pre class="mermaid">
flowchart TB
  classDef step fill:#161b22,stroke:#58a6ff,color:#e6edf3
  classDef ok   fill:#0a1f12,stroke:#3fb950,color:#7ee787
  classDef warn fill:#1f1a0a,stroke:#d29922,color:#ffa657

  A["Input"]:::step --> B{"decision"}:::step
  B -- "ok" --> C["Result"]:::ok
  B -- "fail" --> D["log + bail"]:::warn
  </pre>
</div>
```

Mermaid initialization (one script tag at the bottom of the body):

```html
<script src="https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"></script>
<script>
  mermaid.initialize({
    startOnLoad: true,
    theme: 'dark',
    themeVariables: {
      background: '#0a0d12',
      primaryColor: '#161b22',
      primaryTextColor: '#e6edf3',
      primaryBorderColor: '#58a6ff',
      lineColor: '#8b949e',
      fontFamily: 'ui-sans-serif, system-ui, sans-serif',
      fontSize: '13px',
    },
    flowchart: { curve: 'basis', htmlLabels: true, nodeSpacing: 40, rankSpacing: 50, padding: 10 },
    securityLevel: 'loose',
  });
</script>
```

This is the **one allowed CDN** for long-form HTML. Don't pull anything else (no fonts, no Prism, no Tailwind). Mermaid pays its weight by replacing ASCII art that doesn't survive screenshots.

### Mermaid types worth knowing

- `flowchart LR` / `flowchart TB` — most common; pipelines and architectures.
- `sequenceDiagram` — interactions between services.
- `stateDiagram-v2` — state machines.
- `erDiagram` — relational schemas.

Define `classDef` colors once per diagram and apply with `:::className`. Don't inline styles per node — it's noisy.

## Color and typography

Paleta (the only one to use unless the user asks otherwise):

```
--bg:       #0d1117    body
--bg2:      #161b22    sections / cards
--bg3:      #1c2128    nested surfaces
--text:     #e6edf3    primary text
--text-soft:#c9d1d9    body copy
--muted:    #8b949e    secondary
--muted2:   #6e7681    tertiary

--accent:   #58a6ff    blue (links, primary)
--accent2:  #d2a8ff    purple (code, h3)
--accent3:  #7ee787    green (highlights)

--ok:       #3fb950    success
--warn:     #d29922    warning
--crit:     #f85149    error
--info:     #79c0ff    info

--border:      #30363d
--border-soft: #21262d
```

Type stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif`. For code: `ui-monospace, "SF Mono", Menlo, Consolas, monospace`. Line-height **1.6** for body, **1.55** for code blocks.

Headings: tight letter-spacing (`-.01em` on h1, `-.005em` on h2) reads more "designed" than default.

## Section anatomy

Every long-form section follows the same pattern so the eye can scan:

```html
<section class="sec" id="s07">
  <h2>
    <span class="ord">07</span>
    Section title
    <span class="tag ok">approved</span>   <!-- optional state -->
  </h2>
  <p class="lead">One-paragraph framing.</p> <!-- optional -->

  <h3>Subsection</h3>
  ...
</section>
```

- The `ord` numeric prefix matches the TOC counter (`counter-increment`).
- The optional `tag` chip floats to the right via `margin-left: auto`.
- `lead` is muted and has a dashed bottom border — feels like a deck.

## Code blocks with manual syntax color

No Prism / highlight.js. Use these classes inline:

```html
<pre><code><span class="tok-kw">CREATE TABLE</span> users (
    id <span class="tok-typ">UUID</span> <span class="tok-kw">PRIMARY KEY</span>,
    email <span class="tok-typ">TEXT</span> <span class="tok-kw">NOT NULL</span>,
    age <span class="tok-typ">INTEGER</span>
);
<span class="tok-com">-- index on email</span></code></pre>
```

Token classes available: `tok-kw` (keyword), `tok-typ` (type), `tok-str` (string), `tok-num` (number), `tok-com` (comment), `tok-fn` (function), `tok-id` (identifier). Only highlight what aids comprehension — don't paint every word.

## Tables

Use `<table>` for endpoint lists, parameter references, status matrices. Color HTTP methods to scan fast:

```html
<td class="method"><code>GET</code></td>
<td class="method post"><code>POST</code></td>
<td class="method patch"><code>PATCH</code></td>
<td class="method delete"><code>DELETE</code></td>
```

For two-column "key: value" lists prefer `.key-list` (grid `max-content 1fr`) over a `<dl>` — looks tidier:

```html
<ul class="key-list">
  <li><span class="k">commit</span><span><code>47f0c130</code></span></li>
  <li><span class="k">branch</span><span><code>feature/...</code></span></li>
</ul>
```

## Badges and callouts

**Badges** (`.badge.ok / .warn / .crit / .info / .muted / .purple`) — inline severity tags. Pair them with HTTP status codes, severity levels, or status flags.

**Callouts** — short note boxes with a left border. Four variants:

```html
<div class="callout">          <!-- info (blue) -->
<div class="callout warn">     <!-- amber -->
<div class="callout ok">       <!-- green -->
<div class="callout crit">     <!-- red -->
```

Use sparingly — they lose weight if every paragraph is one.

## Header with gradient

The header gets two radial gradients in opposite corners as a subtle premium touch:

```css
background:
  radial-gradient(circle at 0%   0%, rgba(88,166,255,.07) 0%, transparent 50%),
  radial-gradient(circle at 100% 0%, rgba(210,168,255,.05) 0%, transparent 50%),
  var(--bg);
```

Pairs nicely with all-caps `crumbs` (path/context) before the H1.

## What not to do

- **No emojis as functional UI.** They render inconsistently. Use badges and SVG-like Unicode arrows (`→`, `↗`, `⤓`) only where they add clarity.
- **No external font loading.** Stick to system stacks. The whole point of self-contained HTML is no FOUT.
- **No animations that move on scroll.** Cute on first view, distracting after.
- **No purple/blue gradient text.** Use solid colors for headings. Gradients on body text age fast.
- **No light-mode fallback.** The host renders dark. Adding `@media (prefers-color-scheme: light)` is wasted CSS.

## Checklist before posting

- [ ] H1 + sub paragraph make sense out of context.
- [ ] TOC entries match section ids (and the order of sections).
- [ ] Every Mermaid block has `classDef` colors (else it's monochrome on dark).
- [ ] All anchors work (`scroll-margin-top` on sections).
- [ ] No CDN other than Mermaid (and only if used).
- [ ] Responsive at <980px (TOC moves above content).
- [ ] HTML payload < ~150KB (Mermaid CDN is the bulk).
