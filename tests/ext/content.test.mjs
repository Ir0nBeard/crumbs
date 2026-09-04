/**
 * Merchant-cookie + page-state behaviour tests for ext/content.js.
 *
 * The content script runs ONLY on user-opted-in sites and reads what a page
 * script can legitimately see: the JS-visible mirror cookie `crumbs_jr`, the
 * SDK's localStorage receipt, and heuristic agent signals. The HttpOnly
 * `__Host-crumbs_j` receipt cookie is deliberately never read from JS (the
 * merchant server reads it) — the static guard at the bottom enforces that the
 * extension keeps that posture (no chrome.cookies, no HttpOnly-cookie reads).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createChromeMock } from "./chrome_mock.mjs";
import { loadScript, norm, readExt, tick } from "./helpers.mjs";

const RECEIPT_JSON = JSON.stringify({
  rid: "rct_mirror",
  jid: "j_20260904",
  aid: "a_synergy",
  exp: 1789000000,
  sig: "0xsig",
});

function boot({ cookie = "", receipt = null, ua = "", modelContext = null } = {}) {
  const mock = createChromeMock();
  const localStorage = {
    _data: receipt === null ? new Map() : new Map([["crumbs:receipt", receipt]]),
    getItem(k) {
      return this._data.has(k) ? this._data.get(k) : null;
    },
  };
  const sandbox = {
    chrome: mock.chrome,
    document: {
      cookie,
      modelContext: modelContext?.document ?? undefined,
    },
    navigator: {
      userAgent: ua,
      modelContext: modelContext?.navigator ?? undefined,
    },
    location: { href: "https://shop.example/checkout", origin: "https://shop.example" },
    localStorage,
  };
  loadScript("content.js", sandbox);
  return mock;
}

async function collect(mock) {
  return mock.chrome.runtime.sendMessage({ type: "crumbs_collect" });
}

test("collects mirror cookie, receipt ids, and agent signals from a GPTBot page", async () => {
  const mock = boot({
    cookie: "session=x; crumbs_jr=" + encodeURIComponent("jrnl:abc") + "; theme=dark",
    receipt: RECEIPT_JSON,
    ua: "Mozilla/5.0 (compatible; GPTBot/1.1)",
  });
  const { ok, state } = await collect(mock);
  assert.equal(ok, true);
  assert.equal(state.mirrorCookie, "jrnl:abc");
  assert.deepEqual(norm(state.receipt), {
    rid: "rct_mirror",
    jid: "j_20260904",
    aid: "a_synergy",
    exp: 1789000000,
  });
  assert.equal(state.agentSignals.agentLike, true);
  assert.ok(state.agentSignals.userAgentHits.includes("gptbot"));
  assert.equal(state.agentSignals.webmcp, false);
  assert.equal(state.hasWebmcpTool, false);
});

test("bare page: no cookie, no receipt, no agent signals — nulls, no throw", async () => {
  const mock = boot({ ua: "Mozilla/5.0 (X11; Linux x86_64) Chrome/126 Safari/537.36" });
  const { ok, state } = await collect(mock);
  assert.equal(ok, true);
  assert.equal(state.mirrorCookie, null);
  assert.equal(state.receipt, null);
  assert.deepEqual(norm(state.agentSignals.userAgentHits), []);
  assert.equal(state.agentSignals.agentLike, false);
});

test("malformed receipt JSON in SDK storage degrades to receipt:null (no throw)", async () => {
  const mock = boot({ receipt: "{not json!!", cookie: "crumbs_jr=abc" });
  const { ok, state } = await collect(mock);
  assert.equal(ok, true);
  assert.equal(state.receipt, null);
  assert.equal(state.mirrorCookie, "abc");
});

test("WebMCP context with registerTool reports the crumbs_conversion surface", async () => {
  const mock = boot({
    ua: "Mozilla/5.0",
    modelContext: {
      document: { registerTool: () => {} },
      navigator: undefined,
    },
  });
  const { state } = await collect(mock);
  assert.equal(state.agentSignals.webmcp, true);
  assert.equal(state.hasWebmcpTool, true);
});

test("merchant-cookie posture: extension reads the JS mirror only — never the HttpOnly receipt cookie via chrome.cookies", () => {
  const background = readExt("background.js");
  const content = readExt("content.js");
  const popup = readExt("popup/popup.js");
  const manifest = JSON.parse(readExt("manifest.json"));
  // Doc comments legitimately name the HttpOnly cookie (content.js explains
  // WHY it is unreadable), so check executable code only: strip comments,
  // then assert no reference survives — the cookie stays server-side.
  const stripComments = (src) =>
    src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
  for (const [name, src] of [
    ["background.js", background],
    ["content.js", content],
    ["popup/popup.js", popup],
  ]) {
    assert.ok(
      !stripComments(src).includes("__Host-crumbs_j"),
      `${name} executable code must not read the HttpOnly cookie`
    );
    assert.ok(!src.includes("chrome.cookies"), `${name} must not use chrome.cookies`);
  }
  const declared = JSON.stringify(manifest);
  assert.ok(!declared.includes("cookies"), "manifest must not declare a cookies permission");
  // content.js reads the mirror via document.cookie only
  assert.ok(content.includes("MIRROR_COOKIE"));
  assert.ok(!content.includes('"cookies"'));
});
