/*
 * Progressive enhancement of the view and diff pages; both work without it.
 *
 * Loaded synchronously in <head>, like theme.js, so the history panel's state is
 * on <html data-panel="open|closed"> before first paint. On a wide screen the
 * reader's last choice is remembered under pergam-history-panel; on a narrow
 * one the panel starts closed and opens over the document. Dropdowns (<details
 * class="dropdown">) close on Escape and on a click elsewhere. On the diff page, a
 * section link opens the collapsed section it points to.
 *
 * The pin, archive and tag controls, rendered hidden only when the server offers
 * them, call PATCH /{id}/meta with a JSON body (which a cross-site form cannot send)
 * and show what it answers. Nothing here listens to messages: the framed
 * document cannot reach these controls.
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

  const REFUSED = {
    400: 'The server did not accept that change. Tags are at most 40 characters, at most 20 of them.',
    401: 'The server does not know who you are, so nothing was changed. Sign in and reload the page.',
    403: 'Only the owner of this pergam or one of its collaborators can change it. Nothing was changed.',
    404: 'This pergam no longer exists.',
  };

  const isMeta = (meta) => meta && typeof meta.pinned === 'boolean' && typeof meta.archived === 'boolean'
    && Array.isArray(meta.tags) && meta.tags.every((tag) => typeof tag === 'string');

  const setUpMetaControls = () => {
    const controls = document.querySelector('[data-meta-url]');
    const status = document.querySelector('[data-meta-status]');
    if (!controls || !status) return;
    const url = controls.dataset.metaUrl;
    const flags = Array.from(controls.querySelectorAll('[data-meta-flag]'));
    const tagList = document.querySelector('[data-tags]');
    const tagEdit = document.querySelector('[data-tags-edit]');
    const tagForm = document.querySelector('[data-tags-form]');
    const tagInput = tagForm && tagForm.querySelector('input');
    const busy = (on) => {
      [...flags, ...(tagForm ? tagForm.querySelectorAll('button') : [])].forEach((button) => { button.disabled = on; });
    };
    const ANNOUNCE_DELAY = 100;
    let pending;
    const say = (text, failed) => {
      clearTimeout(pending);
      status.textContent = '';
      status.classList.toggle('meta-status--error', Boolean(failed));
      if (text) pending = setTimeout(() => { status.textContent = text; }, ANNOUNCE_DELAY);
    };

    const show = (meta) => {
      flags.forEach((button) => button.setAttribute('aria-pressed', String(meta[button.dataset.metaFlag])));
      if (tagList) {
        tagList.replaceChildren(...meta.tags.map((tag) => {
          const link = document.createElement('a');
          link.className = 'tag';
          link.href = '/?' + new URLSearchParams({ tag });
          link.textContent = tag;
          return link;
        }));
      }
      if (tagInput) tagInput.defaultValue = tagInput.value = meta.tags.join(', ');
    };

    const patch = async (change) => {
      say('');
      busy(true);
      try {
        const response = await fetch(url, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(change),
          credentials: 'same-origin',
          cache: 'no-store',
        });
        if (!response.ok) {
          say(REFUSED[response.status] || `The server refused the change (${response.status}). Nothing was changed.`, true);
          return false;
        }
        const meta = await response.json();
        if (!isMeta(meta)) throw new TypeError('unexpected answer');
        show(meta);
        return true;
      } catch (error) {
        say('Could not reach the server. Nothing was changed.', true);
        return false;
      } finally {
        busy(false);
      }
    };

    const DONE = {
      pinned: ['Pinned: it shows first on the index.', 'Unpinned.'],
      archived: ['Archived: the index hides it unless archived pergams are shown.', 'Unarchived.'],
    };
    flags.forEach((button) => {
      button.addEventListener('click', async () => {
        const flag = button.dataset.metaFlag;
        const wanted = button.getAttribute('aria-pressed') !== 'true';
        if (await patch({ [flag]: wanted })) say(DONE[flag][wanted ? 0 : 1]);
      });
    });

    if (tagEdit && tagForm && tagInput) {
      const editing = (on) => {
        tagForm.hidden = !on;
        tagEdit.hidden = on;
        if (tagList) tagList.hidden = on;
        if (on) tagInput.focus();
        else tagEdit.focus();
      };
      tagEdit.hidden = false;
      tagEdit.addEventListener('click', () => editing(true));
      const cancel = () => {
        tagForm.reset();
        editing(false);
      };
      tagForm.querySelector('[data-tags-cancel]').addEventListener('click', cancel);
      tagForm.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          cancel();
        }
      });
      tagForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const tags = tagInput.value.split(',').map((tag) => tag.trim()).filter(Boolean);
        if (await patch({ tags })) {
          editing(false);
          say('Tags saved.');
        }
      });
    }
    controls.hidden = false;
  };

  const setUpSectionLinks = () => {
    if (!document.querySelector('.toc')) return;
    const reveal = () => {
      const target = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
      if (target instanceof HTMLDetailsElement) target.open = true;
    };
    addEventListener('hashchange', reveal);
    reveal();
  };

  document.addEventListener('DOMContentLoaded', () => {
    setUpPanel();
    setUpDropdowns();
    setUpMetaControls();
    setUpSectionLinks();
  });
})();
