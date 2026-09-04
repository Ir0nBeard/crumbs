/**
 * Chrome `chrome.*` API mock for headless MV3 extension logic tests.
 *
 * Faithful to the promise/callback semantics the extension actually uses:
 *   - chrome.storage.local/sync get/set accept BOTH the promise style
 *     (await chrome.storage.local.get(key)) and the MV3 callback style
 *     (chrome.storage.local.get(key, (got) => ...)) used in event handlers.
 *   - chrome.permissions.request resolves true/false based on an injectable
 *     decider, recording grants so remove() can revoke them.
 *   - chrome.scripting tracks registered content scripts and rejects
 *     duplicate ids the way the real API does.
 *   - chrome.runtime.onMessage supports async handlers (return true).
 *   - chrome.tabs.query filters {active, currentWindow}; tabs.sendMessage is
 *     injectable (per-tab content-script context lives in the test, not here).
 */
export function createChromeMock(opts = {}) {
  const local = new Map();
  const sync = new Map();
  const grantedPermissions = new Set(opts.grantedPermissions || []);
  const grantedOrigins = new Set(opts.grantedOrigins || []);
  const registeredScripts = [];
  const tabs = (opts.tabs || []).map((t) => ({ ...t }));
  const onMessageListeners = [];
  const onInstalledListeners = [];
  const permissionDecider =
    opts.permissionDecider || ((_request) => true);
  const onTabMessage =
    opts.onTabMessage || (() => Promise.resolve({ ok: false }));

  function readStore(store, keys) {
    if (typeof keys === "string") {
      return store.has(keys) ? { [keys]: store.get(keys) } : {};
    }
    if (Array.isArray(keys)) {
      const out = {};
      for (const k of keys) if (store.has(k)) out[k] = store.get(k);
      return out;
    }
    if (keys && typeof keys === "object") {
      const out = {};
      for (const [k, def] of Object.entries(keys)) {
        out[k] = store.has(k) ? store.get(k) : def;
      }
      return out;
    }
    return Object.fromEntries(store);
  }

  function makeStorage(store) {
    return {
      get(keys, cb) {
        const p = Promise.resolve().then(() => readStore(store, keys));
        if (typeof cb === "function") p.then(cb);
        return p;
      },
      set(items, cb) {
        const p = Promise.resolve().then(() => {
          for (const [k, v] of Object.entries(items)) store.set(k, v);
        });
        if (typeof cb === "function") p.then(cb);
        return p;
      },
      remove(keys, cb) {
        const p = Promise.resolve().then(() => {
          for (const k of [].concat(keys)) store.delete(k);
        });
        if (typeof cb === "function") p.then(cb);
        return p;
      },
    };
  }

  const chrome = {
    storage: {
      local: makeStorage(local),
      sync: makeStorage(sync),
    },
    permissions: {
      request(request) {
        return Promise.resolve().then(() => {
          const ok = permissionDecider(request);
          if (ok) {
            for (const p of request.permissions || []) grantedPermissions.add(p);
            for (const o of request.origins || []) grantedOrigins.add(o);
          }
          return ok;
        });
      },
      remove(request) {
        return Promise.resolve().then(() => {
          for (const p of request.permissions || []) grantedPermissions.delete(p);
          for (const o of request.origins || []) grantedOrigins.delete(o);
          return true;
        });
      },
      contains() {
        return Promise.resolve(true);
      },
    },
    scripting: {
      getRegisteredContentScripts() {
        return Promise.resolve(registeredScripts.map((s) => ({ ...s })));
      },
      registerContentScripts(list) {
        return Promise.resolve().then(() => {
          for (const item of list) {
            if (registeredScripts.some((s) => s.id === item.id)) {
              throw new Error(`Duplicate content script id '${item.id}'`);
            }
            registeredScripts.push({ ...item });
          }
        });
      },
      unregisterContentScripts({ ids }) {
        return Promise.resolve().then(() => {
          for (let i = registeredScripts.length - 1; i >= 0; i--) {
            if (ids.includes(registeredScripts[i].id)) registeredScripts.splice(i, 1);
          }
        });
      },
    },
    tabs: {
      query(info) {
        return Promise.resolve().then(() =>
          tabs.filter((t) => {
            if (info.active && !t.active) return false;
            if (info.currentWindow && !t.currentWindow) return false;
            return true;
          })
        );
      },
      sendMessage(tabId, msg) {
        return Promise.resolve(onTabMessage(tabId, msg));
      },
    },
    runtime: {
      onInstalled: {
        addListener(fn) {
          onInstalledListeners.push(fn);
        },
      },
      onMessage: {
        addListener(fn) {
          onMessageListeners.push(fn);
        },
      },
      sendMessage(msg, sender = {}) {
        return new Promise((resolve) => {
          let done = false;
          const sendResponse = (val) => {
            if (!done) {
              done = true;
              resolve(val);
            }
          };
          for (const fn of [...onMessageListeners]) {
            const ret = fn(msg, sender, sendResponse);
            if (ret === false) {
              /* handler not interested — response stays open for others */
            }
          }
          setTimeout(() => {
            if (!done) {
              done = true;
              resolve(undefined);
            }
          }, 0);
        });
      },
      _fireInstalled() {
        for (const fn of [...onInstalledListeners]) fn({ reason: "install" });
      },
    },
  };

  return {
    chrome,
    _state: {
      get grantedPermissions() {
        return new Set(grantedPermissions);
      },
      get grantedOrigins() {
        return new Set(grantedOrigins);
      },
      get registeredScripts() {
        return registeredScripts.map((s) => ({ ...s }));
      },
      async localGet(key) {
        return (await chrome.storage.local.get(key))[key];
      },
      async syncGet(key) {
        return (await chrome.storage.sync.get(key))[key];
      },
    },
  };
}
