# Distribution channels & publishing runbooks

How each Crumbs component reaches its channel, what artifact it ships, and
what is required before anything is published. The project is open source and
self-hosted; every channel below is an optional distribution surface for
adoption.

**Build everything offline (no publishing):**

```sh
export PATH="$HOME/node/bin:$PATH"   # node >= 18 (SDK build + pack)
python3 scripts/package_release.py
```

Output lands in `dist/release/<version>/` (SDK tarball, Chrome extension zip,
WordPress plugin zip, `SHA256SUMS`, `RELEASE_MANIFEST.json`). The script
single-sources the version from `sdk/package.json` and fails the build if
`ext/manifest.json` or the plugin `readme.txt` stable tag disagrees.

Publishing itself is credential-gated and is **not** done by this script.

---

## Channel matrix

| Channel | Component | Artifact | Status | Gate to publish |
|---|---|---|---|---|
| **npm** | JS SDK (`@crumbs/sdk`) | `crumbs-sdk-<v>.tgz` | artifact-ready, unpublished | npm account token; drop `"private": true` in the release commit |
| **Chrome Web Store** | `Crumbs Journey Viewer` (MV3 ext) | `crumbs-journey-viewer-<v>.zip` | artifact-ready, unpublished | CWS developer account (one-time $5); screenshots; listing copy in `ext/STORE_LISTING.md` |
| **WordPress.org** | `Crumbs Attribution` plugin | `crumbs-attribution-<v>.zip` (wp.org layout) | artifact-ready, unpublished | wp.org plugin submitter account; SVN push; `readme.txt` already wp.org-spec |
| **Shopify App Store** | custom-app scaffold (`shopify/`) | source only | scaffold, not a public app | Shopify Partner account; public listing requires app review |
| **MCP registries** | `mcp/crumbs_mcp.py` (stdio server) | source only | unlisted | Smithery: GitHub OAuth + repo-based registration; mcp.so: form; PulseMCP: GitHub |
| **CDN** | SDK IIFE bundle | `sdk/dist/crumbs.iife.js` | bundle built | automatic once npm/GitHub release exists (jsDelivr, unpkg) |
| **GitHub Releases** | all artifacts | `dist/release/<v>/*` | none yet (no tag) | repo maintainer; attach artifacts to a signed tag release |

---

## Per-channel runbook

### npm — `@crumbs/sdk`

The SDK is an ES module with a prebuilt IIFE (`sdk/dist/crumbs.iife.js`) and
zero runtime dependencies. `sdk/package.json` pins the published file set
(`crumbs.mjs`, `src/`, `dist/`); README and LICENSE ship automatically.

```sh
cd sdk
npm pack --dry-run        # inspect the tarball contents first
# in the release commit: set "private": false
npm publish --access public
```

Verify after publish: `npm view @crumbs/sdk version` and
`npm view @crumbs/sdk dist.tarball`.

### Chrome Web Store — Crumbs Journey Viewer

Build: `python3 scripts/package_release.py` → `crumbs-journey-viewer-<v>.zip`
(manifest at zip root; icons included).

Submit: Chrome Web Store developer dashboard → *New item* → upload the zip.
Store listing copy (name, summary, description, permission justification,
privacy policy pointer) is ready in `ext/STORE_LISTING.md`; `ext/PRIVACY.md`
is the privacy disclosure. The extension requests only `storage` +
`activeTab` at install; `scripting` + per-site host access are optional and
user-triggered — no `cookies` permission, no remote code (MV3).

### WordPress.org — Crumbs Attribution

Build: `python3 scripts/package_release.py` → `crumbs-attribution-<v>.zip`
(plugin lives in a top-level `crumbs-attribution/` folder, the layout
wordpress.org expects).

`wp/crumbs-attribution/readme.txt` is already in directory spec (headers +
Description / Installation / FAQ / Changelog / Upgrade Notice) and the plugin
header declares GPL-2.0-or-later. Publish = SVN push to the directory
(`svn co https://plugins.svn.wordpress.org/crumbs-attribution`), then bump
`Stable tag:` on release.

### Shopify

`shopify/` contains a zero-dependency custom-app scaffold (OAuth
token-exchange). Custom apps are distributed per-store (shareable install
link); a public App Store listing additionally requires a Partner account and
passing app review. No artifact is built yet — the scaffold is the starting
point for both paths.

### MCP registries

`mcp/crumbs_mcp.py` is a stdio JSON-RPC MCP server (tools: `request_journey`,
`verify_receipt`, `declare_conversion`). Registries are mostly GitHub-based:

- **Smithery** — register the repository; the server is launched from the repo.
- **mcp.so / PulseMCP** — directory submissions pointing at the GitHub repo.

An MCP registry entry only makes sense once a ledger instance is publicly
reachable and documented (a default endpoint is intentionally *not* baked
into the code).

### CDN

Once `@crumbs/sdk` is on npm (or a GitHub release exists), the IIFE bundle is
servable directly:

- `https://cdn.jsdelivr.net/npm/@crumbs/sdk@<v>/dist/crumbs.iife.js`
- `https://unpkg.com/@crumbs/sdk@<v>/dist/crumbs.iife.js`

---

## Status truth

Nothing in this repository is published to any third-party channel yet.
"Artifact-ready" means the file builds reproducibly and passes the repository
test suite — it is not a claim of availability on the channel.
