/*
 * The filter dialog of the index. Without it the Filters button is a plain link to
 * /filters, a page with the same form.
 *
 * - [data-filters-open] (the Filters button, "+N more", "Edit filters") opens the
 *   dialog instead of following its link, and pushes a history entry, so the back
 *   button closes it (a reload with the dialog open opens it again, so that entry
 *   still means something). Escape, the close button, a click on the backdrop and
 *   back all discard the draft: the form is put back as the server rendered it.
 * - The page carries an empty dialog: its form comes from `data-src` (/filters/form)
 *   on first open. If that request fails, the link is followed to the /filters page.
 * - Choices are a draft. Each change asks GET /facets for the draft (debounced) and
 *   updates every count and the apply button in place; values with no result are
 *   dimmed and cannot be ticked, unless already ticked.
 * - Apply navigates once, replacing the dialog's history entry, to the canonical URL
 *   the form describes (values sorted by code point within each key, as the server's
 *   links are);
 *   the new page then focuses the "N of M" summary and says it. Enter in a find
 *   field does not apply.
 * - Long lists expand in place; a find field filters a long author or tag list, and
 *   the first Escape in it clears it instead of closing the dialog.
 */
(() => {
  'use strict';
  const APPLIED = 'pergam-filters-applied';
  const DEBOUNCE_MS = 150;
  const dialog = document.querySelector('[data-filters-dialog]');

  const pageLive = () => {
    const region = document.createElement('p');
    region.className = 'sr-only';
    region.setAttribute('aria-live', 'polite');
    document.body.append(region);
    return region;
  };

  const announceApplied = () => {
    let applied = null;
    try {
      applied = sessionStorage.getItem(APPLIED);
      sessionStorage.removeItem(APPLIED);
    } catch (error) {
      return;
    }
    if (!applied) return;
    const results = document.querySelector('[data-results]');
    if (!results) return;
    const region = pageLive();
    results.focus();
    const shown = results.textContent.trim().split(' ')[0];
    setTimeout(() => {
      region.textContent = `${shown} pergam${shown === '1' ? '' : 's'}`;
    }, 100);
  };

  document.addEventListener('DOMContentLoaded', announceApplied);
  if (!dialog) return;

  let form = null;
  let pristine = '';
  let loading = null;
  const part = (selector) => form.querySelector(selector);
  let opener = null;
  let timer = null;
  let pending = null;

  const byCodePoint = (a, b) => {
    const left = Array.from(a);
    const right = Array.from(b);
    for (let i = 0; i < Math.min(left.length, right.length); i += 1) {
      const difference = left[i].codePointAt(0) - right[i].codePointAt(0);
      if (difference) return difference;
    }
    return left.length - right.length;
  };

  const query = () => {
    const values = new Map();
    for (const [key, value] of new FormData(form)) {
      if (['state', 'q', 'sort'].includes(key) && !value) continue;
      if (!values.has(key)) values.set(key, []);
      values.get(key).push(value);
    }
    const pairs = [];
    values.forEach((list, key) => {
      if (['type', 'author', 'tag'].includes(key)) list.sort(byCodePoint);
      list.forEach((value) => pairs.push([key, value]));
    });
    return new URLSearchParams(pairs).toString();
  };

  const load = () => {
    loading =
      loading ||
      fetch(dialog.dataset.src, { headers: { Accept: 'text/html' } }).then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        dialog.innerHTML = await response.text();
        form = dialog.querySelector('[data-filters-form]');
        if (!form) throw new Error('no form');
        pristine = form.innerHTML;
      });
    return loading.catch((error) => {
      loading = null;
      throw error;
    });
  };

  const plural = (count) => `${count} pergam${count === 1 ? '' : 's'}`;

  const showTotal = (total) => {
    const apply = part('[data-filters-apply]');
    part('[data-filters-none]').hidden = total !== 0;
    apply.disabled = total === 0;
    apply.textContent = total === 0 ? 'No matches' : `Show ${plural(total)}`;
    part('[data-filters-live]').textContent = `${plural(total)} match`;
  };

  const showCounts = (facets) => {
    for (const key of ['type', 'author', 'tag']) {
      const counts = new Map((facets[key] || []).map((entry) => [entry.value, entry.count]));
      form.querySelectorAll(`[data-section="${key}"] [data-item]`).forEach((item) => {
        const input = item.querySelector('input');
        const count = counts.get(item.dataset.value) || 0;
        const unavailable = count === 0 && !input.checked;
        const shown = item.querySelector('.pill__label, .arow__name').textContent;
        item.querySelector('[data-count]').textContent = String(count);
        input.setAttribute('aria-label', `${shown}, ${count} pergam${count === 1 ? '' : 's'}${unavailable ? ', unavailable' : ''}`);
        item.classList.toggle('is-zero', unavailable);
        if (unavailable) input.setAttribute('aria-disabled', 'true');
        else input.removeAttribute('aria-disabled');
      });
    }
  };

  const showSelections = () => {
    form.querySelectorAll('[data-section]').forEach((section) => {
      const label = section.querySelector('[data-selected]');
      if (!label) return;
      const count = section.querySelectorAll('input[type="checkbox"]:checked').length;
      label.textContent = count ? `${count} selected` : '';
      section.querySelector('[data-section-clear]').hidden = count === 0;
    });
  };

  const refresh = async () => {
    if (pending) pending.abort();
    const controller = new AbortController();
    pending = controller;
    const foot = part('[data-filters-foot]');
    foot.setAttribute('aria-busy', 'true');
    try {
      const qs = query();
      const response = await fetch(`/facets${qs ? `?${qs}` : ''}`, {
        signal: controller.signal,
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.json();
      showCounts(body.facets);
      showTotal(body.total);
    } catch (error) {
      if (error.name === 'AbortError') return;
      const apply = part('[data-filters-apply]');
      apply.disabled = false;
      apply.textContent = 'Show results';
      part('[data-filters-none]').hidden = true;
    } finally {
      if (pending === controller) {
        pending = null;
        foot.removeAttribute('aria-busy');
      }
    }
  };

  const changed = () => {
    showSelections();
    clearTimeout(timer);
    timer = setTimeout(refresh, DEBOUNCE_MS);
  };

  const close = () => {
    if (!dialog.open) return;
    clearTimeout(timer);
    if (pending) pending.abort();
    dialog.close();
    if (form) form.innerHTML = pristine;
    if (opener && opener.isConnected) opener.focus();
  };

  const dismiss = () => {
    if (history.state && history.state.pergamFilters) history.back();
    else close();
  };

  const firstControl = () =>
    part('input[type="checkbox"]:checked') ||
    part('input[name="state"]:checked:not([value=""])') ||
    part('input[name="type"]') ||
    part('input');

  const open = async (from, push = true) => {
    opener = from.closest('.summary, .empty') ? document.querySelector('.filters-btn') || from : from;
    if (dialog.open) return;
    try {
      await load();
    } catch (error) {
      if (push) window.location.assign(from.href);
      return;
    }
    if (dialog.open) return;
    if (push) history.pushState({ pergamFilters: true }, '');
    dialog.showModal();
    const first = firstControl();
    if (first) first.focus();
  };

  const filterList = (field) => {
    const section = field.closest('[data-section]');
    const needle = field.value.trim().toLowerCase();
    const more = section.querySelector('[data-more]');
    const expanded = more && more.getAttribute('aria-expanded') === 'true';
    let visible = 0;
    section.querySelectorAll('[data-item]').forEach((item) => {
      const matches = item.dataset.value.toLowerCase().includes(needle);
      item.hidden = needle ? !matches : item.hasAttribute('data-extra') && !expanded;
      if (!item.hidden) visible += 1;
    });
    if (more) more.hidden = Boolean(needle);
    const nomatch = section.querySelector('[data-nomatch]');
    nomatch.hidden = !needle || visible > 0;
    nomatch.querySelector('[data-nomatch-text]').textContent = field.value.trim();
  };

  const toggleMore = (button) => {
    const expanded = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(expanded));
    button.textContent = expanded ? button.dataset.lessLabel : button.dataset.moreLabel;
    button.closest('[data-section]').querySelectorAll('[data-extra]').forEach((item) => {
      item.hidden = !expanded;
    });
  };

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-filters-open]');
    if (!trigger || dialog.contains(trigger)) return;
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    open(trigger);
  });

  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) {
      dismiss();
      return;
    }
    const item = event.target.closest('[data-item]');
    if (item) {
      const input = item.querySelector('input');
      const wasChecked = event.target === input ? !input.checked : input.checked;
      if (input.getAttribute('aria-disabled') === 'true' && !wasChecked) event.preventDefault();
      return;
    }
    if (event.target.closest('[data-filters-close]')) {
      dismiss();
    } else if (event.target.closest('[data-filters-reset]')) {
      form.querySelectorAll('input[type="checkbox"]').forEach((input) => {
        input.checked = false;
      });
      const active = part('input[name="state"][value=""]');
      if (active) active.checked = true;
      changed();
    } else if (event.target.closest('[data-section-clear]')) {
      const section = event.target.closest('[data-section]');
      section.querySelectorAll('input[type="checkbox"]').forEach((input) => {
        input.checked = false;
      });
      changed();
      const first = section.querySelector('input');
      if (first) first.focus();
    } else if (event.target.closest('[data-more]')) {
      toggleMore(event.target.closest('[data-more]'));
    }
  });

  dialog.addEventListener('change', (event) => {
    if (event.target.matches('[data-find]')) return;
    changed();
  });
  dialog.addEventListener('input', (event) => {
    if (event.target.matches('[data-find]')) filterList(event.target);
  });
  dialog.addEventListener('keydown', (event) => {
    if (!event.target.matches('[data-find]')) return;
    if (event.key === 'Enter') {
      event.preventDefault();
    } else if (event.key === 'Escape' && event.target.value) {
      event.preventDefault();
      event.stopPropagation();
      event.target.value = '';
      filterList(event.target);
    }
  });
  dialog.addEventListener('submit', (event) => {
    event.preventDefault();
    if (part('[data-filters-apply]').disabled) return;
    try {
      sessionStorage.setItem(APPLIED, '1');
    } catch (error) {
      /* the new page just does not move the focus */
    }
    const qs = query();
    location.replace(`/${qs ? `?${qs}` : ''}`);
  });

  dialog.addEventListener('cancel', (event) => {
    event.preventDefault();
    dismiss();
  });
  const trigger = () => document.querySelector('.filters-btn');
  window.addEventListener('popstate', () => {
    if (history.state && history.state.pergamFilters) {
      if (!dialog.open && trigger()) open(trigger(), false);
    } else {
      close();
    }
  });
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) close();
  });
  if (history.state && history.state.pergamFilters && trigger()) open(trigger(), false);
})();
