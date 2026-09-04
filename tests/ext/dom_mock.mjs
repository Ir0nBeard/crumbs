/**
 * Minimal DOM mock for headless popup-logic tests.
 *
 * Implements just what ext/popup/popup.js touches: getElementById returning
 * elements with textContent/disabled, and click listeners that tests can fire.
 * No layout, no stylesheets — this is a logic harness, not a renderer.
 */
export function createDomMock(ids) {
  const elements = new Map();
  for (const id of ids) {
    const el = {
      id,
      textContent: "",
      disabled: false,
      _listeners: {},
      addEventListener(type, fn) {
        this._listeners[type] = fn;
      },
      click() {
        const fn = this._listeners.click;
        if (fn) return fn();
      },
    };
    elements.set(id, el);
  }
  return {
    document: {
      getElementById(id) {
        return elements.get(id) || null;
      },
    },
    element(id) {
      return elements.get(id);
    },
  };
}

/** DOM ids referenced by ext/popup/popup.html + popup.js. */
export const POPUP_IDS = [
  "siteOrigin",
  "toggleSite",
  "siteHint",
  "mirror",
  "receipt",
  "agents",
  "webmcp",
  "refresh",
  "verify",
  "verifyResult",
];
