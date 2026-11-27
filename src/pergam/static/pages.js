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
 * and show what it answers. Opening the tag editor re-reads GET /{id}/meta first, so
 * the set it replaces is the current one, not the one the page was loaded with. Nothing here listens to messages: the framed
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
    const showCurrent = () => {
      const current = panel.querySelector('.history__link[aria-current]');
      if (!current || root.dataset.panel !== 'open') return;
      const box = panel.getBoundingClientRect();
      const item = current.closest('li').getBoundingClientRect();
      if (item.top < box.top || item.bottom > box.bottom) {
        panel.scrollTop += item.top - box.top - Math.max(0, (box.height - item.height) / 2);
      }
    };
    const set = (state, { focus = false } = {}) => {
      root.dataset.panel = state;
      if (wide.matches) remember(state);
      sync();
      if (focus && state === 'open') panel.querySelector('h2').focus({ preventScroll: true });
      if (focus && state === 'closed') toggle.focus();
      showCurrent();
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
      showCurrent();
    });
    showCurrent();
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
    400: 'The server did not accept that change (400). Nothing was changed.',
    401: 'The server does not know who you are, so nothing was changed. Sign in and reload the page.',
    403: 'Only the owner of this pergam or one of its collaborators can change it. Nothing was changed.',
    404: 'This pergam no longer exists.',
  };

  const REFUSED_TAGS = 'The server did not accept these tags: at most 20, each at most 40 characters. Nothing was changed.';
  const PIN_LIMIT = 'As many pergams are pinned as this server allows: unpin another one first';
  const MAX_REASON = 300;

  /* The server's one-line reason from an error body: the standard library's page
     ("Message: …") parsed as an inert document, or the reason field of a JSON body; only text. */
  const reasonOf = async (response) => {
    try {
      const body = await response.text();
      if ((response.headers.get('Content-Type') || '').includes('json')) {
        const data = JSON.parse(body);
        const reason = data && (data.message || data.error || data.detail);
        return typeof reason === 'string' ? reason.trim().slice(0, MAX_REASON) : '';
      }
      const page = new DOMParser().parseFromString(body, 'text/html');
      const line = Array.from(page.querySelectorAll('p'), (node) => node.textContent.trim())
        .find((text) => text.startsWith('Message: '));
      return line ? line.slice('Message: '.length).replace(/\.$/, '').trim().slice(0, MAX_REASON) : '';
    } catch (error) {
      return '';
    }
  };

  const isMeta = (meta) => meta && typeof meta.pinned === 'boolean' && typeof meta.archived === 'boolean'
    && Array.isArray(meta.tags) && meta.tags.every((tag) => typeof tag === 'string');

  const setUpMetaControls = () => {
    const controls = document.querySelector('[data-meta-url]');
    const status = document.querySelector('[data-meta-status]');
    if (!controls || !status) return;
    const url = controls.dataset.metaUrl;
    const flags = Array.from(document.querySelectorAll('[data-meta-flag]'));
    const pins = flags.filter((button) => button.dataset.metaFlag === 'pinned');
    const tagLists = Array.from(document.querySelectorAll('[data-tags]'));
    const barTags = document.querySelector('.bar__tags');
    const tagEdits = Array.from(document.querySelectorAll('[data-tags-edit]'));
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
      pins.forEach((pin) => {
        pin.setAttribute('aria-disabled', String(meta.archived));
        if (meta.archived) pin.setAttribute('aria-describedby', 'pin-note');
        else pin.removeAttribute('aria-describedby');
      });
      tagLists.forEach((list) => {
        list.replaceChildren(...meta.tags.map((tag) => {
          const link = document.createElement('a');
          link.className = 'tag';
          link.href = '/?' + new URLSearchParams({ tag });
          link.textContent = tag;
          return link;
        }));
      });
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
        if (response.status === 409 && change.pinned === true) {
          say(`${(await reasonOf(response)) || PIN_LIMIT}. Nothing was changed.`, true);
          return false;
        }
        if (!response.ok) {
          const refused = response.status === 400 && 'tags' in change ? REFUSED_TAGS : REFUSED[response.status];
          say(refused || `The server refused the change (${response.status}). Nothing was changed.`, true);
          return false;
        }
        const meta = await response.json();
        if (!isMeta(meta)) throw new TypeError('unexpected answer');
        show(meta);
        return meta;
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
    const ARCHIVED_UNPINNED = 'Archived and unpinned: the index hides it unless archived pergams are shown.';
    const PIN_ARCHIVED = 'Archived pergams cannot be pinned: unarchive it first.';
    flags.forEach((button) => {
      button.addEventListener('click', async () => {
        const flag = button.dataset.metaFlag;
        if (button.getAttribute('aria-disabled') === 'true') {
          say(PIN_ARCHIVED);
          return;
        }
        const wanted = button.getAttribute('aria-pressed') !== 'true';
        const wasPinned = pins.some((pin) => pin.getAttribute('aria-pressed') === 'true');
        const meta = await patch({ [flag]: wanted });
        if (!meta) return;
        const unpinned = flag === 'archived' && wanted && wasPinned && !meta.pinned;
        say(unpinned ? ARCHIVED_UNPINNED : DONE[flag][wanted ? 0 : 1]);
      });
    });

    if (tagEdits.length && tagForm && tagInput) {
      const barEdit = tagEdits.find((button) => !button.closest('details'));
      let openedBy = barEdit;
      const editing = (on) => {
        tagForm.hidden = !on;
        if (barEdit) barEdit.hidden = on;
        if (barTags) barTags.hidden = on;
        if (on) {
          tagInput.focus();
          return;
        }
        const menu = openedBy && openedBy.closest('details');
        (menu ? menu.querySelector('summary') : openedBy).focus();
      };
      if (barEdit) barEdit.hidden = false;
      const reread = async () => {
        try {
          const response = await fetch(url, { cache: 'no-store', credentials: 'same-origin' });
          const meta = response.ok ? await response.json() : null;
          if (!isMeta(meta)) return;
          const typed = tagInput.value;
          const untouched = typed === tagInput.defaultValue;
          show(meta);
          if (!untouched) tagInput.value = typed;
        } catch (error) {
          /* keep editing what the page already shows */
        }
      };
      tagEdits.forEach((button) => {
        button.addEventListener('click', () => {
          openedBy = button;
          const menu = button.closest('details');
          if (menu) menu.open = false;
          editing(true);
          reread();
        });
      });
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
    document.querySelectorAll('[data-meta-item]').forEach((item) => { item.hidden = false; });
  };

  const setUpSectionLinks = () => {
    if (!document.querySelector('.toc')) return;
    const open = (hash) => {
      let id;
      try {
        id = decodeURIComponent(hash.slice(1));
      } catch (error) {
        return;
      }
      const target = id && document.getElementById(id);
      if (target instanceof HTMLDetailsElement) target.open = true;
    };
    addEventListener('hashchange', () => open(location.hash));
    document.querySelectorAll('.toc a[href^="#"]').forEach((link) => {
      link.addEventListener('click', () => open(link.getAttribute('href')));
    });
    open(location.hash);
  };

  document.addEventListener('DOMContentLoaded', () => {
    setUpPanel();
    setUpDropdowns();
    setUpMetaControls();
    setUpSectionLinks();
  });
})();
