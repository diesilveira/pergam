/*
 * pergam frame bridge, appended to /{id}/v{n}/content.
 *
 * Talks to the view page around the document with postMessage only, so it keeps
 * working once the frame is sandboxed without allow-same-origin. It reports the
 * document's title and fragment, follows a fragment or a print request from the
 * view page, and sends links that leave the document to the top window.
 */
(() => {
  'use strict';
  const parent = window.parent;
  if (parent === window) return;

  const MAX_LENGTH = 2048;
  const origin = new URL(location.href).origin;
  const send = (type, value) => parent.postMessage({ pergam: 1, type, value }, origin);
  const withoutHash = (href) => href.split('#')[0];

  addEventListener('hashchange', () => send('hash', location.hash));

  addEventListener('message', (event) => {
    if (event.source !== parent) return;
    const data = event.data;
    if (!data || typeof data !== 'object' || data.pergam !== 1 || typeof data.value !== 'string') return;
    if (data.type === 'hash' && data.value.startsWith('#') && data.value.length <= MAX_LENGTH) {
      if (data.value !== location.hash) location.replace(data.value);
    } else if (data.type === 'print') {
      window.print();
    }
  });

  addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 'p') {
      event.preventDefault();
      window.print();
    }
  });

  document.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target instanceof Element ? event.target.closest('a[href], area[href]') : null;
    if (!link || link.hasAttribute('target') || link.hasAttribute('download')) return;
    if (document.querySelector('base[target]')) return;
    const url = new URL(link.href, location.href);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return;
    if (withoutHash(url.href) === withoutHash(location.href)) return;
    link.setAttribute('target', '_top');
  });

  send('title', document.title);
  send('ready', '');
})();
