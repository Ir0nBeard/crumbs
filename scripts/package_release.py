#!/usr/bin/env python3
"""Build store-ready distribution artifacts for Crumbs (offline, no publishing).

Produces dist/release/<version>/ containing:
  - crumbs-sdk-<version>.tgz            npm SDK tarball (npm pack, respects sdk/package.json "files")
  - crumbs-journey-viewer-<version>.zip Chrome MV3 extension (manifest.json at zip root)
  - crumbs-attribution-<version>.zip    WordPress plugin in wp.org layout (top-level crumbs-attribution/)
  - SHA256SUMS                          sha256 per artifact
  - RELEASE_MANIFEST.json               machine-readable inventory + version cross-checks

Version is single-sourced from sdk/package.json and cross-checked against
ext/manifest.json and wp/crumbs-attribution/readme.txt (Stable tag), so a
forgotten bump fails the build instead of shipping a mismatched release.

Nothing is uploaded or published: distribution channels are credential-gated
steps documented in docs/LISTINGS.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SDK_DIR = ROOT / "sdk"
EXT_DIR = ROOT / "ext"
WP_DIR = ROOT / "wp" / "crumbs-attribution"
WP_VENDOR_IIFE = WP_DIR / "vendor" / "crumbs-sdk" / "crumbs.iife.js"
# Dev/docs files that must not ship inside the store artifact.
EXT_EXCLUDE = {"README.md", "STORE_LISTING.md", "make_icons.py"}
SKIP_PARTS = {"__pycache__", ".DS_Store", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def wp_stable_tag(readme: Path) -> str:
    text = readme.read_text(encoding="utf-8")
    m = re.search(r"^Stable tag:\s*(\S+)", text, re.MULTILINE)
    if not m:
        raise SystemExit(f"FATAL: no 'Stable tag' in {readme}")
    return m.group(1)


def version_checks() -> dict:
    """Single-source version from the SDK package; cross-check every other copy."""
    sdk_ver = read_json(SDK_DIR / "package.json")["version"]
    ext_ver = read_json(EXT_DIR / "manifest.json")["version"]
    wp_ver = wp_stable_tag(WP_DIR / "readme.txt")
    checks = {
        "sdk_package_json": sdk_ver,
        "ext_manifest_json": ext_ver,
        "wp_readme_stable_tag": wp_ver,
    }
    mismatches = {k: v for k, v in checks.items() if v != sdk_ver}
    if mismatches:
        raise SystemExit(
            f"FATAL: version mismatch vs sdk/package.json ({sdk_ver}): {mismatches}"
        )
    return checks


def iter_files(base: Path, exclude: set[str] | None = None):
    exclude = exclude or set()
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_PARTS]
        for name in sorted(files):
            if name in SKIP_PARTS or name.endswith(tuple(SKIP_SUFFIXES)):
                continue
            if name in exclude:
                continue
            yield Path(root) / name


def zip_tree(base: Path, out_zip: Path, prefix: str = "", exclude: set[str] | None = None) -> int:
    """Zip base/* into out_zip. With prefix="", files land at zip root."""
    count = 0
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in iter_files(base, exclude):
            rel = src.relative_to(base)
            arc = (Path(prefix) / rel).as_posix() if prefix else rel.as_posix()
            zf.write(src, arc)
            count += 1
    return count


def run(cmd: list[str], cwd: Path) -> None:
    env = dict(os.environ)
    print(f"  $ {' '.join(cmd)}  (cwd: {cwd.relative_to(ROOT)})")
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def main() -> int:
    if shutil.which("npm") is None:
        print("WARN: npm not on PATH — SDK build/pack steps skipped.", file=sys.stderr)
        return 2

    checks = version_checks()
    version = checks["sdk_package_json"]
    dest = ROOT / "dist" / "release" / version
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    artifacts: list[dict] = []

    # 1) SDK: rebuild the IIFE bundle, then pack the npm tarball.
    print("== SDK ==")
    run(["npm", "run", "build"], SDK_DIR)
    # Keep the plugin's vendored bundle in sync with the freshly built one
    # (idempotent when the tree is already current).
    shutil.copyfile(SDK_DIR / "dist" / "crumbs.iife.js", WP_VENDOR_IIFE)
    run(["npm", "pack", "--pack-destination", str(dest)], SDK_DIR)
    for tgz in sorted(dest.glob("*.tgz")):
        artifacts.append(
            {
                "file": tgz.name,
                "kind": "npm-sdk-tarball",
                "size": tgz.stat().st_size,
                "sha256": sha256_file(tgz),
            }
        )
        print(f"  -> {tgz.name} ({tgz.stat().st_size} bytes)")

    # 2) Chrome MV3 extension zip (manifest at root).
    print("== Chrome extension ==")
    ext_zip = dest / f"crumbs-journey-viewer-{version}.zip"
    n = zip_tree(EXT_DIR, ext_zip, exclude=EXT_EXCLUDE)
    artifacts.append(
        {
            "file": ext_zip.name,
            "kind": "chrome-mv3-extension",
            "files": n,
            "size": ext_zip.stat().st_size,
            "sha256": sha256_file(ext_zip),
        }
    )
    print(f"  -> {ext_zip.name} ({n} files, {ext_zip.stat().st_size} bytes)")

    # 3) WordPress plugin zip, wp.org layout (top-level plugin folder).
    print("== WordPress plugin ==")
    wp_zip = dest / f"crumbs-attribution-{version}.zip"
    n = zip_tree(WP_DIR, wp_zip, prefix="crumbs-attribution")
    artifacts.append(
        {
            "file": wp_zip.name,
            "kind": "wordpress-plugin",
            "files": n,
            "size": wp_zip.stat().st_size,
            "sha256": sha256_file(wp_zip),
        }
    )
    print(f"  -> {wp_zip.name} ({n} files, {wp_zip.stat().st_size} bytes)")

    # 4) Checksums + manifest.
    sums = ["# Crumbs release artifacts — sha256"]
    for a in artifacts:
        sums.append(f"{a['sha256']}  {a['file']}")
    (dest / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")

    manifest = {
        "project": "crumbs",
        "version": version,
        "version_checks": checks,
        "built_at_utc": subprocess.run(
            ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True
        ).stdout.strip(),
        "artifacts": artifacts,
        "note": "Build artifacts only. Publishing is a separate credential-gated "
        "step — see docs/LISTINGS.md.",
    }
    (dest / "RELEASE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    print(f"\n== Done: {dest.relative_to(ROOT)} ==")
    for a in artifacts:
        print(f"  {a['file']}  sha256={a['sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
