/*
 * Theme choice for every pergam page. Loaded synchronously in <head>, before the
 * body is parsed, so a remembered choice is on <html> before first paint.
 * Without a remembered choice the system's colour scheme applies (see pergam.css).
 * Any element with a data-theme-toggle attribute switches themes; it starts
 * hidden and is shown here, so pages without script never show a dead button.
 */
(() => {
  const KEY = 'pergam-theme';
  const root = document.documentElement;
  const system = matchMedia('(prefers-color-scheme: dark)');
  const valid = (value) => value === 'light' || value === 'dark';

  const remembered = () => {
    try {
      const value = localStorage.getItem(KEY);
      return valid(value) ? value : null;
    } catch (error) {
      return null;
    }
  };
  const remember = (value) => {
    try {
      localStorage.setItem(KEY, value);
    } catch (error) {
      /* private mode: the choice lasts for this page only */
    }
  };
  const current = () => root.dataset.theme || (system.matches ? 'dark' : 'light');

  const label = () => {
    const next = current() === 'dark' ? 'light' : 'dark';
    document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
      button.hidden = false;
      button.setAttribute('aria-label', `Switch to ${next} theme`);
      button.title = `Switch to ${next} theme`;
    });
  };

  const saved = remembered();
  if (saved) root.dataset.theme = saved;

  document.addEventListener('click', (event) => {
    if (!event.target.closest('[data-theme-toggle]')) return;
    const next = current() === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    remember(next);
    label();
  });
  system.addEventListener('change', label);
  document.addEventListener('DOMContentLoaded', label);
})();
