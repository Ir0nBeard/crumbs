=== Crumbs Attribution ===
Contributors: crumbs
Tags: attribution, agent, ai, commerce, consent
Requires at least: 6.0
Tested up to: 6.7
Requires PHP: 7.4
Stable tag: 0.1.0
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Consent-native agent-journey attribution for WordPress/WooCommerce merchants. Vendored SDK, no third-party remote code, no tracking without consent.

== Description ==

Crumbs Attribution records agent-driven journeys (signed attribution receipts)
and stamps attributed conversions at checkout, so referring AI agents (and
their human owners) can be credited and paid — via licensed payout rails.

* **Consent-native**: receipts are issued only after a recorded consent basis
  (ePrivacy Art 5(3)). No consent, no receipt, no cookie.
* **Vendored SDK**: the JavaScript SDK ships inside this plugin (no executable
  code from third-party systems — wordpress.org guideline compliant).
* **HttpOnly receipt cookie**: journey receipts are set server-side as
  `__Host-crumbs_j` (Secure, HttpOnly, SameSite=Lax) with a short-TTL JS mirror.
* **WebMCP-ready**: the SDK registers the `crumbs_conversion` tool when the
  browser exposes `document.modelContext`.
* **No float**: this plugin never holds or moves funds; payouts are scheduled
  and settled by licensed rails (x402/USDC facilitator or Stripe Connect).

== Installation ==

1. Upload the `crumbs-attribution` folder to `/wp-content/plugins/`, or zip and
   install via Plugins → Add New.
2. Activate the plugin.
3. Settings → Crumbs Attribution: enter your merchant ID (m_...) and ledger
   API URL.
4. Wire your CMP: hook the `crumbs_consent_status` filter to return
   `granted`/`denied` from your consent provider.

== Frequently Asked Questions ==

= Does this plugin track visitors without consent? =

No. Receipt issuance is gated on `crumbs_consent_status()` (default: no
consent signal → no issuance). The SDK also refuses to call the ledger before
consent.

= Does the plugin load code from your servers? =

No. The SDK is vendored inside the plugin (`vendor/crumbs-sdk/`) and updated
via plugin releases. The only network calls are server-side requests from your
WordPress site to the ledger API you configure.

= Does the plugin hold money? =

No. It only records attribution and requests payout *scheduling* from the
ledger. Settlement runs through licensed rails.

== Changelog ==

= 0.1.0 =
* Initial scaffold: settings page, consent-gated server-side journey issuance,
  vendored SDK, HttpOnly receipt cookie, uninstall cleanup.

== Upgrade Notice ==

= 0.1.0 =
Local MVP scaffold — not yet listed in the wordpress.org directory.
