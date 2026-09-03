#!/usr/bin/env node
/**
 * Build the IIFE bundle from the single source of truth (src/crumbs-core.cjs).
 *
 *   node scripts/build-iife.mjs        -> writes dist/crumbs.iife.js
 *
 * The core is already UMD (it attaches to globalThis.CrumbsCore when not in
 * CommonJS); the IIFE wrapper additionally exposes the documented global:
 *   window.Crumbs = { createCrumbs }
 *
 * No bundler, no deps — a plain wrapper, so the bundle stays auditable.
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const core = readFileSync(resolve(here, "../src/crumbs-core.cjs"), "utf8");

const banner = `/*! Crumbs attribution SDK v0.1.0 — IIFE bundle (generated from src/crumbs-core.js).
 * Consent-native agent-journey attribution. No dependencies. */
`;

const iife = `${banner}(function (global) {
${core}
global.Crumbs = global.Crumbs || {};
global.Crumbs.createCrumbs = (global.CrumbsCore || {}).createCrumbs;
})(typeof window !== "undefined" ? window : typeof globalThis !== "undefined" ? globalThis : this);
`;

const out = resolve(here, "../dist/crumbs.iife.js");
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, iife);
console.log("wrote", out, `(${iife.length} bytes)`);
