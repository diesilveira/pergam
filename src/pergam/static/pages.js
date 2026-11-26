/*
 * Progressive enhancement of the view and diff pages; both work without it.
 *
 * Loaded synchronously in <head>, like theme.js, so the history panel's state is
 * on <html data-panel="open|closed"> before first paint. On a wide screen the
 * reader's last choice is remembered under pergam-history-panel; on a narrow
 * one the panel starts closed and opens over the document. Dropdowns (<details
 * class="dropdown">) close on Escape and on a click elsewhere. This script never
 * listens to messages from the framed document.
 */
(() => {
  'use strict';
  const PANEL_KEY = 'pergam-history-panel';
  const root = document.documentElement;
  const wide = matchMedia('(min-width: 861px)');

  const remembered = () => {
    try {
      const value = localStorage.getItem(PANEL_KEY);
      return value === 'open' || value === 'closed' ? value : null;
    } catch (error) {
      return null;
    }
  };
  const remember = (value) => {
    try {
      localStorage.setItem(PANEL_KEY, value);
    } catch (error) {
      /* private mode: the choice lasts for this page only */
    }
  };
  const initialPanel = () => (wide.matches ? remembered() || 'open' : 'closed');
  root.dataset.panel = initialPanel();

  const setUpPanel = () => {
    const panel = document.getElementById('panel');
    const toggle = document.querySelector('[data-panel-toggle]');
    const close = document.querySelector('[data-panel-close]');
    if (!panel || !toggle) return;
    const sync = () => toggle.setAttribute('aria-expanded', String(root.dataset.panel === 'open'));
    const set = (state, { focus = false } = {}) => {
      root.dataset.panel = state;
      if (wide.matches) remember(state);
      sync();
      if (focus && state === 'open') panel.querySelector('h2').focus();
      if (focus && state === 'closed') toggle.focus();
    };
    panel.querySelector('h2').tabIndex = -1;
    toggle.hidden = false;
    if (close) close.hidden = false;
    sync();
    toggle.addEventListener('click', () => set(root.dataset.panel === 'open' ? 'closed' : 'open', { focus: !wide.matches }));
    if (close) close.addEventListener('click', () => set('closed', { focus: true }));
    panel.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !wide.matches && root.dataset.panel === 'open') set('closed', { focus: true });
    });
    wide.addEventListener('change', () => {
      root.dataset.panel = initialPanel();
      sync();
    });
  };

  const setUpDropdowns = () => {
    const dropdowns = Array.from(document.querySelectorAll('details.dropdown'));
    if (!dropdowns.length) return;
    dropdowns.forEach((dropdown) => {
      dropdown.addEventListener('toggle', () => {
        if (dropdown.open) dropdowns.forEach((other) => { if (other !== dropdown) other.open = false; });
      });
    });
    document.addEventListener('click', (event) => {
      dropdowns.forEach((dropdown) => { if (dropdown.open && !dropdown.contains(event.target)) dropdown.open = false; });
    });
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      const open = dropdowns.find((dropdown) => dropdown.open);
      if (!open) return;
      open.open = false;
      open.querySelector('summary').focus();
    });
  };

  document.addEventListener('DOMContentLoaded', () => {
    setUpPanel();
    setUpDropdowns();
  });
})();
