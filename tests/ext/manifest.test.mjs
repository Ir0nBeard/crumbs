/**
 * MV3 manifest conformance tests for ext/.
 *
 * Machine-checkable store rules: manifest_version 3, minimal install-time
 * permissions (storage + activeTab only), optional scripting + host access,
 * NO cookies permission, NO static content_scripts (the extension registers
 * content scripts dynamically per opt-in site — the whole point of its
 * privacy posture), declared files present, and no remote-code patterns.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import path from "node:path";
import { EXT_DIR, readExt } from "./helpers.mjs";

const manifest = JSON.parse(readExt("manifest.json"));

test("manifest is MV3 with minimal install-time permissions", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions.sort(), ["activeTab", "storage"]);
  assert.ok(
    manifest.optional_permissions.includes("scripting"),
    "scripting must be optional (granted per opt-in)"
  );
  assert.ok(
    Array.isArray(manifest.optional_host_permissions) &&
      manifest.optional_host_permissions.includes("https://*/*"),
    "host access must be optional, never install-time"
  );
  assert.equal(manifest.host_permissions, undefined, "no static host_permissions");
});

test("no cookies permission and no static content_scripts (dynamic registration only)", () => {
  const declared = JSON.stringify(manifest);
  assert.ok(!declared.includes('"cookies"'), "no cookies permission");
  assert.equal(
    manifest.content_scripts,
    undefined,
    "content scripts must be registered dynamically, not declared statically"
  );
});

test("all referenced files exist", () => {
  const refs = [
    manifest.background?.service_worker,
    manifest.action?.default_popup,
    ...Object.values(manifest.icons || {}),
  ].filter(Boolean);
  for (const rel of refs) {
    assert.ok(existsSync(path.join(EXT_DIR, rel)), `missing file: ${rel}`);
  }
});

test("no remote code: no eval, no new Function, no remote script src", () => {
  for (const rel of ["background.js", "content.js", "popup/popup.js"]) {
    const src = readExt(rel);
    assert.ok(!/\beval\s*\(/.test(src), `${rel}: eval() found`);
    assert.ok(!/new\s+Function\s*\(/.test(src), `${rel}: new Function() found`);
  }
  const popupHtml = readExt("popup/popup.html");
  assert.ok(!/<script[^>]+src=["']https?:/.test(popupHtml), "popup.html loads no remote scripts");
  assert.ok(popupHtml.includes('src="popup.js"'), "popup.html loads the local popup.js");
});
