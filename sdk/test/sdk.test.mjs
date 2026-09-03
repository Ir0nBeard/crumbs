/**
 * SDK tests — node --test, zero dependencies.
 *
 * A tiny fake Crumbs API server (node:http) stands in for the ledger; the SDK
 * under test is the ES module (crumbs.mjs) plus the IIFE bundle (loaded in a
 * fresh vm context to prove the global `Crumbs.createCrumbs` surface).
 */
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import vm from "node:vm";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { createCrumbs } from "../crumbs.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const IIFE = readFileSync(resolve(here, "../dist/crumbs.iife.js"), "utf8");

const RECEIPT = {
  v: 1,
  rid: "rct_01M169J4VN8PHRJ78VS5ZD7TWQX",
  jid: "jrn_01M169J4VN8PHRJ78VS5ZD7TWQY",
  aid: "ag_testagent1234567890",
  mid: "m_testmerchant1234567",
  oid: "",
  cv: 0,
  cur: "USD",
  crb: 1200,
  ntb: 1500,
  sf: "browser",
  nc: "aGVsbG8td29ybGQtdGVzdA",
  iat: 1787991495,
  exp: 1790583495,
  kid: 1,
  sig: "test-signature-placeholder-not-validated-by-sdk",
};

/** Spin up a fake ledger server; returns {url, close, calls}. */
function fakeServer() {
  const calls = { journeys: 0, conversions: 0, verifies: 0, conversionsByIdentity: new Map() };
  let idCounter = 0;
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      res.setHeader("content-type", "application/json");
      const url = new URL(req.url, "http://fake");
      if (req.method === "POST" && url.pathname === "/v1/journeys") {
        calls.journeys++;
        res.writeHead(201);
        res.end(
          JSON.stringify({
            receipt: JSON.stringify(RECEIPT),
            rid: RECEIPT.rid,
            journey_id: RECEIPT.jid,
            agent_id: RECEIPT.aid,
            exp: RECEIPT.exp,
            consent: { basis: "explicit", recorded: true },
          })
        );
        return;
      }
      if (req.method === "POST" && url.pathname === "/v1/conversions") {
        calls.conversions++;
        const idem = req.headers["idempotency-key"];
        if (idem && calls.conversionsByIdentity.has(idem)) {
          res.writeHead(200);
          res.end(JSON.stringify({ ...calls.conversionsByIdentity.get(idem), idempotent: true }));
          return;
        }
        const out = {
          conversion_id: "c_conv" + ++idCounter,
          rid: RECEIPT.rid,
          oid: JSON.parse(body).order_id,
          status: "pending",
          awaiting: "merchant order webhook",
        };
        if (idem) calls.conversionsByIdentity.set(idem, out);
        res.writeHead(201);
        res.end(JSON.stringify(out));
        return;
      }
      if (req.method === "POST" && url.pathname === "/v1/verify") {
        calls.verifies++;
        const receipt = JSON.parse(body).receipt;
        res.writeHead(200);
        res.end(JSON.stringify({ valid: receipt === JSON.stringify(RECEIPT), rid: RECEIPT.rid }));
        return;
      }
      res.writeHead(404);
      res.end(JSON.stringify({ detail: { code: "NOT_FOUND" } }));
    });
  });
  return new Promise((resolvePromise) => {
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      resolvePromise({ url: `http://127.0.0.1:${port}`, close: () => server.close(), calls });
    });
  });
}

const memoryStorage = () => {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
};

test("consent gate: no receipt issuance before consent", async () => {
  const server = await fakeServer();
  try {
    const crumbs = createCrumbs({
      apiUrl: server.url,
      merchantId: RECEIPT.mid,
      fetch: (u, o) => fetch(u, o),
    });
    await assert.rejects(() => crumbs.requestJourney(), (err) => err.code === "CONSENT_REQUIRED");
    assert.equal(server.calls.journeys, 0, "no journey request may leave before consent");
  } finally {
    server.close();
  }
});

test("requestJourney with consent issues and stores the receipt", async () => {
  const server = await fakeServer();
  const storage = memoryStorage();
  try {
    const crumbs = createCrumbs({
      apiUrl: server.url,
      merchantId: RECEIPT.mid,
      storage,
      fetch: (u, o) => fetch(u, o),
    });
    crumbs.setConsent("granted");
    const journey = await crumbs.requestJourney({ surface: "browser" });
    assert.equal(journey.rid, RECEIPT.rid);
    assert.equal(server.calls.journeys, 1);
    assert.equal(crumbs.getReceipt(), JSON.stringify(RECEIPT));
    assert.equal(crumbs._state.journeyId, RECEIPT.jid);
    // localStorage holds an id-marker ONLY — never the bearer claim
    const marker = JSON.parse(storage.getItem("crumbs:receipt"));
    assert.deepEqual(Object.keys(marker).sort(), ["aid", "exp", "jid", "rid"]);
    assert.equal(marker.rid, RECEIPT.rid);
    assert.ok(!marker.sig && !marker.nc, "sig/nonce must not persist to storage");
  } finally {
    server.close();
  }
});

test("consentProvider hook resolves consent before issuance", async () => {
  const server = await fakeServer();
  try {
    let asked = 0;
    const crumbs = createCrumbs({
      apiUrl: server.url,
      merchantId: RECEIPT.mid,
      consentProvider: async () => {
        asked++;
        return "granted";
      },
      fetch: (u, o) => fetch(u, o),
    });
    await crumbs.requestJourney();
    assert.equal(asked, 1);
    assert.equal(server.calls.journeys, 1);
  } finally {
    server.close();
  }
});

test("stampConversion sends Idempotency-Key <rid>:<oid> and is retry-safe", async () => {
  const server = await fakeServer();
  try {
    const crumbs = createCrumbs({ apiUrl: server.url, merchantId: RECEIPT.mid });
    crumbs.setReceipt(JSON.stringify(RECEIPT));
    const first = await crumbs.stampConversion({
      orderId: "ord_42",
      cartValueMinorUnits: 9900,
      currency: "usd",
    });
    assert.equal(first.conversion_id.startsWith("c_"), true);
    const second = await crumbs.stampConversion({
      orderId: "ord_42",
      cartValueMinorUnits: 9900,
      currency: "USD",
    });
    assert.equal(second.conversion_id, first.conversion_id, "idempotent retry returns same cid");
    assert.equal(second.idempotent, true);
    assert.equal(server.calls.conversions, 2);
  } finally {
    server.close();
  }
});

test("verifyReceipt hits POST /v1/verify", async () => {
  const server = await fakeServer();
  try {
    const crumbs = createCrumbs({ apiUrl: server.url });
    crumbs.setReceipt(JSON.stringify(RECEIPT));
    const result = await crumbs.verifyReceipt();
    assert.equal(result.valid, true);
    assert.equal(server.calls.verifies, 1);
  } finally {
    server.close();
  }
});

test("carriers: header value, x402 referral field, builder code", () => {
  const crumbs = createCrumbs({});
  crumbs.setReceipt(JSON.stringify(RECEIPT));
  assert.equal(crumbs.getHeaderValue(), JSON.stringify(RECEIPT));
  const ref = crumbs.getX402ReferralField();
  assert.deepEqual(ref, { referral: { ref: RECEIPT.jid, provider: "crumbs" } });
  assert.equal(crumbs.getBuilderCode(), "bc_crumbs");
  assert.ok(crumbs.getBuilderCode().length <= 32, "ERC-8021 32-char code limit");
});

test("agent signal detection (heuristic)", () => {
  const crumbs = createCrumbs({});
  const originalUA = globalThis.navigator?.userAgent;
  try {
    Object.defineProperty(globalThis, "navigator", {
      value: { userAgent: "Mozilla/5.0 (X11; Linux) ChatGPT-User/1.0" },
      configurable: true,
    });
    const signals = crumbs.detectAgentSignals();
    assert.equal(signals.userAgentHits.length >= 1, true);
    assert.equal(signals.agentLike, true);
  } finally {
    if (originalUA !== undefined) {
      Object.defineProperty(globalThis, "navigator", { value: { userAgent: originalUA }, configurable: true });
    }
  }
});

test("WebMCP: registers crumbs_conversion via document.modelContext when available", async () => {
  const crumbs = createCrumbs({});
  const registered = [];
  const fakeMC = {
    registerTool: async (tool) => {
      registered.push(tool);
    },
  };
  try {
    Object.defineProperty(globalThis, "document", {
      value: { modelContext: fakeMC },
      configurable: true,
    });
    const ok = await crumbs.registerWebmcpTool();
    assert.equal(ok, true);
    assert.equal(registered.length, 1);
    assert.equal(registered[0].name, "crumbs_conversion");
    assert.ok(registered[0].handler, "tool carries a handler");
  } finally {
    delete globalThis.document;
  }
});

test("WebMCP: graceful no-op without modelContext", async () => {
  const crumbs = createCrumbs({});
  const ok = await crumbs.registerWebmcpTool();
  assert.equal(ok, false);
});

test("IIFE bundle exposes window.Crumbs.createCrumbs", () => {
  const sandbox = { console };
  sandbox.window = sandbox; // window === globalThis inside the vm
  sandbox.globalThis = sandbox;
  vm.runInNewContext(IIFE, sandbox);
  assert.equal(typeof sandbox.window.Crumbs.createCrumbs, "function");
  const crumbs = sandbox.window.Crumbs.createCrumbs({});
  assert.equal(typeof crumbs.requestJourney, "function");
  assert.equal(typeof crumbs.stampConversion, "function");
  assert.equal(typeof crumbs.verifyReceipt, "function");
});

test("declarative forms: binds annotated checkout forms", () => {
  const crumbs = createCrumbs({});
  const boundForms = [];
  const fakeForm = {
    getAttribute: (name) =>
      name === "webmcp" ? "" : name === "toolname" ? "crumbs_conversion" : null,
    setAttribute: () => {},
    addEventListener: (ev, fn) => boundForms.push({ ev, fn }),
    elements: {
      order_id: { value: "ord_decl" },
      cart_value_minor_units: { value: "1234" },
      currency: { value: "EUR" },
    },
    submit: () => {},
  };
  const fakeDocument = {
    querySelectorAll: () => [fakeForm],
  };
  try {
    Object.defineProperty(globalThis, "document", { value: fakeDocument, configurable: true });
    const bound = crumbs.bindDeclarativeForms();
    assert.equal(bound, 1);
    assert.equal(boundForms[0].ev, "submit");
    assert.equal(typeof boundForms[0].fn, "function");
  } finally {
    delete globalThis.document;
  }
});
