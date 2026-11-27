/*
 * Keyboard shortcuts for every pergam page: "/" focuses the top bar's search
 * field and Escape clears it (a second Escape leaves it). Shortcuts never fire
 * while the reader is typing in a field or holds a modifier key.
 */
(() => {
  const typing = (element) =>
    element instanceof HTMLElement &&
    (element.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName));

  document.addEventListener('keydown', (event) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    const field = document.querySelector('[data-search]');
    if (!field) return;
    if (event.key === '/' && !typing(event.target)) {
      event.preventDefault();
      field.focus();
      field.select();
    } else if (event.key === 'Escape' && event.target === field) {
      event.preventDefault();
      if (field.value) field.value = '';
      else field.blur();
    }
  });
})();
