/*
 * Theme choice for every pergam page. Loaded synchronously in <head>, before the
 * body is parsed, so a remembered choice is on <html> before first paint.
 * The choice cycles system -> light -> dark -> system; "system" is no stored value
 * and no data-theme, so the colour scheme of the OS applies (see pergam.css).
 * Any element with a data-theme-toggle attribute cycles it; it starts hidden and
 * is shown here, so pages without script never show a dead button. Other open
 * tabs follow a change through the storage event.
 */
(() => {
  const KEY = 'pergam-theme';
  const ORDER = ['system', 'light', 'dark'];
  const GLYPHS = { system: '◐', light: '☀', dark: '☾' };
  const root = document.documentElement;

  const remembered = () => {
    try {
      const value = localStorage.getItem(KEY);
      return value === 'light' || value === 'dark' ? value : 'system';
    } catch (error) {
      return 'system';
    }
  };
  const remember = (mode) => {
    try {
      if (mode === 'system') localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, mode);
    } catch (error) {
      /* private mode: the choice lasts for this page only */
    }
  };
  const apply = (mode) => {
    if (mode === 'system') delete root.dataset.theme;
    else root.dataset.theme = mode;
  };
  const current = () => root.dataset.theme || 'system';
  const next = () => ORDER[(ORDER.indexOf(current()) + 1) % ORDER.length];

  const label = () => {
    const mode = current();
    const text = `Theme: ${mode}. Switch to ${next()}`;
    document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
      button.hidden = false;
      button.dataset.themeMode = mode;
      button.setAttribute('aria-label', text);
      button.title = text;
      const glyph = button.querySelector('[aria-hidden]');
      if (glyph) glyph.textContent = GLYPHS[mode];
    });
  };

  apply(remembered());

  document.addEventListener('click', (event) => {
    if (!event.target.closest('[data-theme-toggle]')) return;
    const mode = next();
    apply(mode);
    remember(mode);
    label();
  });
  addEventListener('storage', (event) => {
    if (event.key !== null && event.key !== KEY) return;
    apply(remembered());
    label();
  });
  document.addEventListener('DOMContentLoaded', label);
})();
