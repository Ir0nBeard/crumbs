/**
 * Shared helpers for headless extension tests: loading plain MV3 scripts in a
 * vm context with mocked globals, plus a small async tick.
 */
import vm from "node:vm";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const EXT_DIR = path.resolve(__dirname, "../../ext");

export function readExt(rel) {
  return readFileSync(path.join(EXT_DIR, rel), "utf8");
}

/**
 * Run an extension source file inside a fresh vm context whose globals are the
 * provided sandbox. Top-level function declarations become context properties,
 * so tests can call e.g. ctx.enableForSite(...) directly.
 */
export function loadScript(rel, sandbox) {
  const source = readExt(rel);
  const ctx = vm.createContext({
    URL,
    setTimeout,
    clearTimeout,
    console,
    ...sandbox,
  });
  vm.runInContext(source, ctx, { filename: `ext/${rel}` });
  return ctx;
}

/** Flush pending promise microtasks + setTimeout(0) trampolines. */
export function tick(ms = 5) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Normalise a value that may originate inside a vm context (different realm,
 * so deepStrictEqual prototype checks fail on otherwise-identical data) back
 * to plain host-realm JSON data before comparing.
 */
export function norm(v) {
  if (v === undefined) return undefined;
  return JSON.parse(JSON.stringify(v));
}
