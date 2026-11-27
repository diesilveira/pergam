/*
 * The index page's own behaviour. Loaded synchronously in <head>, so a remembered
 * density and a collapsed filter column are on <html> before first paint.
 * - [data-density-toggle] switches compact rows and remembers the choice;
 * - below 860px the filter column (details.filters) starts collapsed;
 * - "Load more" ([data-load-more]) appends the next page instead of leaving this one;
 * - the pinned row ([data-pins]) gets previous/next buttons that scroll it by one
 *   view of cards and are disabled at the ends (focus moves to the other one first,
 *   so it never drops to the page), and a focused card scrolls into view.
 * Without script the column stays open, rows keep their size, "Load more" is a
 * plain link to the next page and the pinned row scrolls natively.
 */
(() => {
  const KEY = 'pergam-density';
  const root = document.documentElement;
  const narrow = matchMedia('(max-width: 860px)');

  const remembered = () => {
    try {
      return localStorage.getItem(KEY) === 'compact';
    } catch (error) {
      return false;
    }
  };
  const remember = (compact) => {
    try {
      localStorage.setItem(KEY, compact ? 'compact' : 'comfortable');
    } catch (error) {
      /* private mode: the choice lasts for this page only */
    }
  };

  if (remembered()) root.dataset.density = 'compact';
  if (narrow.matches) root.dataset.filters = 'collapsed';

  const density = () => {
    const button = document.querySelector('[data-density-toggle]');
    if (!button) return;
    const show = () => button.setAttribute('aria-pressed', String(root.dataset.density === 'compact'));
    button.hidden = false;
    show();
    button.addEventListener('click', () => {
      const compact = root.dataset.density !== 'compact';
      if (compact) root.dataset.density = 'compact';
      else delete root.dataset.density;
      remember(compact);
      show();
    });
  };

  const filters = () => {
    const column = document.querySelector('details.filters');
    delete root.dataset.filters;
    if (!column) return;
    const fit = () => {
      column.open = !narrow.matches;
    };
    fit();
    narrow.addEventListener('change', fit);
  };

  const append = (next) => {
    const groups = document.querySelector('[data-groups]');
    const incoming = next.querySelector('[data-groups]');
    const firstNew = [];
    for (const group of Array.from(incoming ? incoming.children : [])) {
      const last = groups.lastElementChild;
      if (last && last.dataset.group === group.dataset.group) {
        const rows = Array.from(group.querySelectorAll('.list > li'));
        last.querySelector('.list').append(...rows);
        firstNew.push(rows[0]);
      } else {
        groups.append(group);
        firstNew.push(group.querySelector('.list > li'));
      }
    }
    const link = document.querySelector('[data-load-more]');
    const following = next.querySelector('[data-load-more]');
    if (following) link.replaceWith(following);
    else link.remove();
    const added = next.querySelectorAll('[data-groups] .list > li').length;
    const status = document.querySelector('[data-load-status]');
    if (status) status.textContent = `${added} more loaded${following ? '' : ', that is all'}`;
    const first = firstNew.find(Boolean);
    if (first) first.querySelector('.row__title').focus();
  };

  const loadMore = () => {
    document.addEventListener('click', async (event) => {
      const link = event.target.closest('[data-load-more]');
      if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (link.getAttribute('aria-busy') === 'true') return;
      link.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch(link.href, { headers: { Accept: 'text/html' } });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        append(new DOMParser().parseFromString(await response.text(), 'text/html'));
      } catch (error) {
        window.location.assign(link.href);
      }
    });
  };

  const pins = () => {
    const row = document.querySelector('[data-pins]');
    const previous = document.querySelector('[data-pins-prev]');
    const next = document.querySelector('[data-pins-next]');
    if (!row || !previous || !next) return;
    const still = matchMedia('(prefers-reduced-motion: reduce)');
    const behavior = () => (still.matches ? 'auto' : 'smooth');
    const sync = () => {
      const overflows = row.scrollWidth > row.clientWidth + 1;
      previous.hidden = next.hidden = !overflows;
      const ends = new Map([
        [previous, row.scrollLeft <= 1],
        [next, row.scrollLeft + row.clientWidth >= row.scrollWidth - 1],
      ]);
      ends.forEach((atEnd, button) => {
        if (!atEnd) button.disabled = false;
      });
      ends.forEach((atEnd, button) => {
        if (!atEnd) return;
        const other = button === previous ? next : previous;
        if (document.activeElement === button && !other.disabled) other.focus();
        button.disabled = true;
      });
    };
    previous.addEventListener('click', () => row.scrollBy({ left: -row.clientWidth, behavior: behavior() }));
    next.addEventListener('click', () => row.scrollBy({ left: row.clientWidth, behavior: behavior() }));
    row.addEventListener('scroll', sync, { passive: true });
    row.addEventListener('focusin', (event) => {
      const card = event.target.closest('.pin');
      if (card) card.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: behavior() });
    });
    new ResizeObserver(sync).observe(row);
    sync();
  };

  document.addEventListener('DOMContentLoaded', () => {
    density();
    filters();
    loadMore();
    pins();
  });
})();
