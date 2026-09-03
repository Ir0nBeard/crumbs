#!/usr/bin/env node
/**
 * Crumbs for Shopify — custom-app pilot scaffold (v0.1, local only).
 *
 * Implements the Shopify OAuth **token-exchange** flow (custom app shape) with
 * ZERO dependencies (Node >= 18 global fetch + node:http + node:crypto). It is
 * a STUB in the sense that no real merchant credentials exist and no API calls
 * beyond the token exchange are wired — see README.md for what comes next.
 *
 * Flow (custom apps, shopify.dev/docs/apps/auth):
 *   1. /install?shop=<myshop>.myshopify.com&timestamp=..&hmac=..
 *        -> validate shop + (when the app secret is configured) the install
 *           HMAC; mint a random per-install state bound to that shop
 *        -> redirect to Shopify authorize?state=<state>
 *   2. Shopify redirects back to /callback?code=..&shop=..&state=..
 *        -> validate the shop against the allowlist regex AND against the
 *           state binding (never trust a raw shop parameter alone)
 *        -> POST /admin/oauth/access_token {client_id, client_secret, code}
 *           (only after both checks pass — the client_secret never leaves for
 *           an unvalidated host) -> access_token (in-memory only; STUB)
 *
 * Env:
 *   SHOPIFY_API_KEY / SHOPIFY_API_SECRET / SHOPIFY_SCOPES / SHOPIFY_REDIRECT_URI
 *   PORT (default 3000)
 *
 * OPSEC: real secrets never committed; this file reads them from env only.
 * The P3 N2 hard gate (shop regex on /callback + random state + HMAC) is
 * implemented and active even with empty env — no real creds exist today, but
 * the pattern that could exfiltrate a secret to an arbitrary host is closed.
 */
import http from "node:http";
import crypto from "node:crypto";

const {
  SHOPIFY_API_KEY = "",
  SHOPIFY_API_SECRET = "",
  SHOPIFY_SCOPES = "read_orders,read_products",
  SHOPIFY_REDIRECT_URI = "http://localhost:3000/callback",
  PORT = 3000,
} = process.env;

// Allowlisted shop shape (P3 N2): only *.myshopify.com subdomains are ever
// contacted. Applied on BOTH /install and /callback — never derived from raw
// user input on the callback path.
const SHOP_REGEX = /^[a-z0-9-]+\.myshopify\.com$/;

// Per-install OAuth state: random nonce bound to the exact shop that started
// the install. /callback only proceeds when the returned state matches this
// binding (CSRF protection + exact-match shop validation, P3 N2).
const pendingStates = new Map(); // state -> { shop, expiresAt }
const STATE_TTL_MS = 10 * 60 * 1000;

// STUB store: real deployments persist tokens securely (KV/DB), keyed by shop.
const tokens = new Map();

function newState(shop) {
  const state = crypto.randomBytes(16).toString("hex");
  pendingStates.set(state, { shop, expiresAt: Date.now() + STATE_TTL_MS });
  return state;
}

function consumeState(state, shop) {
  const entry = pendingStates.get(state);
  if (!entry) return false;
  pendingStates.delete(state); // one-time use
  if (Date.now() > entry.expiresAt) return false;
  return entry.shop === shop; // exact match against the OAuth state binding
}

/**
 * Verify the Shopify install/callback HMAC (shopify.dev/docs/apps/auth/oauth):
 * HMAC-SHA256 of the sorted query params (excluding hmac/signature), hex,
 * compared with the `hmac` param. Only possible once SHOPIFY_API_SECRET is
 * configured — the gate is inert-but-honest while no real app exists.
 */
function verifyHmac(params) {
  if (!SHOPIFY_API_SECRET) return false; // no secret configured -> no claim
  const hmac = params.get("hmac");
  if (!hmac) return false;
  const pairs = [];
  for (const [k, v] of params) {
    if (k === "hmac" || k === "signature") continue;
    pairs.push(`${k}=${v}`);
  }
  pairs.sort();
  const digest = crypto
    .createHmac("sha256", SHOPIFY_API_SECRET)
    .update(pairs.join("&"))
    .digest("hex");
  const a = Buffer.from(digest);
  const b = Buffer.from(hmac);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function authorizeUrl(shop, state) {
  const params = new URLSearchParams({
    client_id: SHOPIFY_API_KEY,
    scope: SHOPIFY_SCOPES,
    redirect_uri: SHOPIFY_REDIRECT_URI,
    state,
  });
  return `https://${shop}/admin/oauth/authorize?${params}`;
}

async function exchangeCode(shop, code) {
  const resp = await fetch(`https://${shop}/admin/oauth/access_token`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      client_id: SHOPIFY_API_KEY,
      client_secret: SHOPIFY_API_SECRET,
      code,
    }),
  });
  if (!resp.ok) {
    throw new Error(`token exchange failed: ${resp.status} ${await resp.text()}`);
  }
  return resp.json(); // { access_token, scope }
}

function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (url.pathname === "/health") {
    return send(res, 200, { status: "ok", app: "crumbs-shopify-stub" });
  }

  if (url.pathname === "/install") {
    const shop = url.searchParams.get("shop");
    if (!shop || !SHOP_REGEX.test(shop)) {
      return send(res, 400, { error: "invalid shop parameter" });
    }
    // HMAC-gate the install request when the app secret is configured
    // (shopify requires this before trusting an install request).
    if (SHOPIFY_API_SECRET && !verifyHmac(url.searchParams)) {
      return send(res, 403, { error: "invalid install HMAC" });
    }
    const state = newState(shop);
    res.writeHead(302, { location: authorizeUrl(shop, state) });
    return res.end();
  }

  if (url.pathname === "/callback") {
    const shop = url.searchParams.get("shop");
    const code = url.searchParams.get("code");
    const state = url.searchParams.get("state");
    // Hard gate (P3 N2): the shop host is trusted ONLY after the allowlist
    // regex AND the exact-match state binding pass — never raw user input.
    if (!shop || !SHOP_REGEX.test(shop)) {
      return send(res, 400, { error: "invalid shop parameter" });
    }
    if (!code) return send(res, 400, { error: "missing code" });
    if (!state || !consumeState(state, shop)) {
      return send(res, 403, { error: "invalid or expired state — reinstall the app" });
    }
    // If the app secret is configured, also HMAC-gate the callback itself.
    if (SHOPIFY_API_SECRET && !verifyHmac(url.searchParams)) {
      return send(res, 403, { error: "invalid callback HMAC" });
    }
    try {
      const { access_token } = await exchangeCode(shop, code);
      tokens.set(shop, access_token); // STUB: secure persistence + rotation
      return send(res, 200, {
        shop,
        token_exchanged: true,
        next_steps: [
          "register webhook (orders/updated) for conversion finalization",
          "install the Crumbs SDK snippet on the storefront theme (consent-gated)",
          "surface attribution settings in the app UI",
        ],
      });
    } catch (e) {
      return send(res, 502, { error: String(e.message) });
    }
  }

  return send(res, 404, { error: "not found" });
});

server.listen(PORT, () => {
  console.log(`Crumbs Shopify stub listening on http://localhost:${PORT}`);
  console.log(`Install URL: http://localhost:${PORT}/install?shop=<your-shop>.myshopify.com`);
  if (!SHOPIFY_API_KEY || !SHOPIFY_API_SECRET) {
    console.warn("WARNING: SHOPIFY_API_KEY / SHOPIFY_API_SECRET unset — token exchange will fail (expected: no real app exists yet)");
  }
});
