/*
 * pergam storage stand-in, inlined at the start of /{id}/v{n}/content.
 *
 * In the sandbox the document has an opaque origin, where reading localStorage or
 * sessionStorage throws. Documents that use them unguarded (a theme toggle, a
 * remembered tab) get an in-memory Storage instead, emptied when the page unloads.
 */
(() => {
  for (const name of ['localStorage', 'sessionStorage']) {
    try { if (window[name]) continue; } catch {}
    const items = new Map();
    const storage = {
      get length() { return items.size; },
      key: (index) => [...items.keys()][index] ?? null,
      getItem: (key) => (items.has(String(key)) ? items.get(String(key)) : null),
      setItem: (key, value) => { items.set(String(key), String(value)); },
      removeItem: (key) => { items.delete(String(key)); },
      clear: () => { items.clear(); },
    };
    const named = (key) => typeof key === 'string' && !(key in storage);
    const view = new Proxy(storage, {
      get: (target, key) => (named(key) ? target.getItem(key) : Reflect.get(target, key)),
      set: (target, key, value) => (named(key) ? (target.setItem(key, value), true) : Reflect.set(target, key, value)),
      deleteProperty: (target, key) => (named(key) ? (target.removeItem(key), true) : Reflect.deleteProperty(target, key)),
      has: (target, key) => (named(key) ? items.has(key) : key in target),
      ownKeys: () => [...items.keys()],
      getOwnPropertyDescriptor: (target, key) => (named(key) && items.has(key)
        ? { value: items.get(key), writable: true, enumerable: true, configurable: true }
        : undefined),
    });
    try { Object.defineProperty(window, name, { value: view, configurable: true }); } catch {}
  }
})();
