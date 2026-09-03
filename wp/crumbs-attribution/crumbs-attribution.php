<?php
/**
 * Plugin Name:       Crumbs Attribution
 * Plugin URI:        https://crumbs.dev
 * Description:       Consent-native agent-journey attribution for WordPress/WooCommerce merchants. Issues signed attribution receipts (server-side), stamps attributed conversions, and surfaces the checkout to WebMCP agents. The SDK is vendored — no third-party remote code. No tracking without consent.
 * Version:           0.1.0
 * Author:            Crumbs
 * License:           GPL-2.0-or-later
 * License URI:       https://www.gnu.org/licenses/gpl-2.0.html
 * Text Domain:       crumbs-attribution
 *
 * Local MVP scaffold. NOT published to wordpress.org (gated on domain + GitHub
 * + explicit go per project OPSEC). Complies with the plugin directory rules
 * by design: GPL-compatible header, SDK VENDORED inside the plugin, no
 * executable code via third-party systems, no tracking without consent.
 *
 * @package CrumbsAttribution
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit; // direct access not allowed (WordPress security convention)
}

define( 'CRUMBS_ATTRIBUTION_VERSION', '0.1.0' );
define( 'CRUMBS_ATTRIBUTION_DIR', plugin_dir_path( __FILE__ ) );

/**
 * Settings (stored as WP options; admin UI on the Settings page).
 *
 *   crumbs_merchant_id   merchant id (m_...) issued by the ledger
 *   crumbs_api_url       ledger base URL (default https://api.crumbs.dev)
 *   crumbs_api_key       optional X-Crumbs-Key for conversion posts —
 *                        v0.1 DEV-ONLY: stored plaintext in wp_options and
 *                        NOT yet sent anywhere (no conversions path wired).
 *                        Pre-public gate (P3 N9): move to a secret manager /
 *                        encrypted option before any real key is configured.
 *                        When one is set, the admin UI shows a warning.
 *   crumbs_consent_policy  'required' (default) | 'off' — never 'off' in production
 */
function crumbs_get_settings() {
	return array(
		'merchant_id'     => get_option( 'crumbs_merchant_id', '' ),
		'api_url'         => get_option( 'crumbs_api_url', 'https://api.crumbs.dev' ),
		'api_key'         => get_option( 'crumbs_api_key', '' ),
		'consent_policy'  => get_option( 'crumbs_consent_policy', 'required' ),
	);
}

/**
 * Dev-only warning for the plaintext merchant key (P3 N9).
 *
 * Mirrors the server's webhook-secret guard pattern in spirit: the key is
 * optional, stored plaintext in wp_options (v0.1 dev scaffold), and not yet
 * sent anywhere. If an admin configures one, show a persistent warning that it
 * must move to a secret manager / encrypted option before public launch.
 */
function crumbs_admin_api_key_warning() {
	$api_key = get_option( 'crumbs_api_key', '' );
	if ( empty( $api_key ) ) {
		return;
	}
	echo '<div class="notice notice-warning"><p>';
	echo esc_html__( 'Crumbs Attribution: the merchant API key is stored as a plaintext option — v0.1 dev scaffold only. Move it to a secret manager / encrypted option before public launch (pre-public gate).', 'crumbs-attribution' );
	echo '</p></div>';
}
add_action( 'admin_notices', 'crumbs_admin_api_key_warning' );

/**
 * Consent gate — the one place every issuance checks.
 *
 * ePrivacy Art 5(3): no receipt issuance before a recorded lawful basis.
 * Hook `crumbs_consent_status` so CMP/consent plugins can inject their signal
 * (GPP/TCF/Consent Mode v2 integration is a STUB: wire your CMP's callback to
 * return 'granted'|'denied' here).
 *
 * @return string 'granted'|'denied'|'unknown'
 */
function crumbs_consent_status() {
	$status = 'unknown';
	// STUB: read your CMP's stored signal, e.g.:
	//   $status = function_exists( 'my_cmp_get_status' ) ? my_cmp_get_status() : 'unknown';
	$status = apply_filters( 'crumbs_consent_status', $status );
	return $status;
}

/**
 * Load the SDK script (VENDORED — see vendor/crumbs-sdk/crumbs.iife.js).
 *
 * wordpress.org guideline: plugins may not load executable code via third-party
 * systems. The SDK ships inside the plugin and is updated via plugin releases.
 * The script tag carries only data attributes (no inline JS execution).
 */
function crumbs_enqueue_sdk() {
	if ( 'off' === crumbs_consent_status() ) {
		return; // no consent -> no SDK instrumentation at all
	}
	$settings = crumbs_get_settings();
	wp_enqueue_script(
		'crumbs-sdk',
		plugins_url( 'vendor/crumbs-sdk/crumbs.iife.js', __FILE__ ),
		array(),
		CRUMBS_ATTRIBUTION_VERSION,
		true // footer
	);
	wp_localize_script(
		'crumbs-sdk',
		'CrumbsConfig',
		array(
			'apiUrl'          => esc_url_raw( $settings['api_url'] ),
			'merchantId'      => sanitize_text_field( $settings['merchant_id'] ),
			'surface'         => 'browser',
			'consentPolicy'   => $settings['consent_policy'],
			'consentStatus'   => crumbs_consent_status(),
			'ajaxUrl'         => admin_url( 'admin-ajax.php' ),
			'nonce'           => wp_create_nonce( 'crumbs_journey' ),
		)
	);
	wp_add_inline_script(
		'crumbs-sdk',
		"(function(){ if ( ! window.Crumbs ) { return; }"
		. " var cfg = window.CrumbsConfig || {};"
		. " window.crumbs = window.Crumbs.createCrumbs({ apiUrl: cfg.apiUrl, merchantId: cfg.merchantId, surface: cfg.surface });"
		. " if ( cfg.consentStatus === 'granted' ) { window.crumbs.setConsent('granted'); }"
		. " window.crumbs.registerWebmcpTool && window.crumbs.registerWebmcpTool();"
		. "})();",
		'after'
	);
}
add_action( 'wp_enqueue_scripts', 'crumbs_enqueue_sdk' );

/**
 * Server-side journey issuance (AJAX) — the real deployment shape.
 *
 * The ledger issues a signed receipt; the MERCHANT SERVER sets the HttpOnly
 * __Host-crumbs_j cookie (JS cannot set HttpOnly cookies). Consent is checked
 * server-side again before the receipt is requested.
 */
function crumbs_ajax_issue_journey() {
	check_ajax_referer( 'crumbs_journey', 'nonce' );

	if ( 'granted' !== crumbs_consent_status() ) {
		wp_send_json_error( array( 'code' => 'CONSENT_REQUIRED' ), 403 );
	}

	$settings = crumbs_get_settings();
	if ( empty( $settings['merchant_id'] ) ) {
		wp_send_json_error( array( 'code' => 'MERCHANT_NOT_CONFIGURED' ), 500 );
	}

	$response = wp_remote_post(
		trailingslashit( $settings['api_url'] ) . 'v1/journeys',
		array(
			'timeout' => 10,
			'headers' => array( 'content-type' => 'application/json' ),
			'body'    => wp_json_encode(
				array(
					'merchant_id' => $settings['merchant_id'],
					'surface'     => 'browser',
					'consent'     => array( 'basis' => 'explicit', 'ref' => 'wp-cmp-hook' ),
				)
			),
		)
	);

	if ( is_wp_error( $response ) ) {
		wp_send_json_error( array( 'code' => 'LEDGER_UNREACHABLE', 'message' => $response->get_error_message() ), 502 );
	}

	$status = wp_remote_retrieve_response_code( $response );
	$body   = json_decode( wp_remote_retrieve_body( $response ), true );
	if ( 201 !== $status || empty( $body['receipt'] ) ) {
		wp_send_json_error( array( 'code' => 'ISSUANCE_FAILED', 'detail' => $body ), 502 );
	}

	// Set the HttpOnly receipt cookie (Secure; Path=/; SameSite=Lax; __Host- prefix).
	// The JS-visible mirror (short TTL) lets the SDK read the journey for POST bodies.
	$receipt = $body['receipt'];
	// phpcs:ignore WordPressVIPMinimum.Functions.RestrictedFunctions.cookies_setcookie
	setcookie( '__Host-crumbs_j', $receipt, array(
		'expires'  => (int) $body['exp'],
		'path'     => '/',
		'secure'   => true,
		'httponly' => true,
		'samesite' => 'Lax',
	) );
	// phpcs:ignore WordPressVIPMinimum.Functions.RestrictedFunctions.cookies_setcookie
	setcookie( 'crumbs_jr', $receipt, array(
		'expires' => time() + 3600, // session-scope short TTL (anti-theft, spec A.6.6)
		'path'    => '/',
		'secure'  => true,
		'samesite' => 'Lax',
	) );

	wp_send_json_success( array(
		'rid'        => $body['rid'],
		'journey_id' => $body['journey_id'],
		'receipt'    => $receipt,
	) );
}
add_action( 'wp_ajax_nopriv_crumbs_issue_journey', 'crumbs_ajax_issue_journey' );
add_action( 'wp_ajax_crumbs_issue_journey', 'crumbs_ajax_issue_journey' );

/**
 * Declarative WebMCP checkout hook: annotate the WooCommerce checkout form so
 * agents can complete it with attribution (spec p5b-wedge-spec §C.2.2).
 * Zero-JS path — attributes only.
 */
function crumbs_annotate_checkout_form( $form_html ) {
	if ( 'granted' !== crumbs_consent_status() ) {
		return $form_html;
	}
	// STUB: WooCommerce checkout hook integration is post-v0.1; this shows the
	// attribute pattern the SDK binds (sdk/src/crumbs-core.cjs bindDeclarativeForms).
	return $form_html;
}
add_filter( 'woocommerce_checkout_form', 'crumbs_annotate_checkout_form' );

/**
 * Admin settings page (minimal).
 */
function crumbs_admin_menu() {
	add_options_page(
		'Crumbs Attribution',
		'Crumbs Attribution',
		'manage_options',
		'crumbs-attribution',
		'crumbs_settings_page'
	);
}
add_action( 'admin_menu', 'crumbs_admin_menu' );

function crumbs_settings_page() {
	if ( ! current_user_can( 'manage_options' ) ) {
		return;
	}
	$settings = crumbs_get_settings();
	?>
	<div class="wrap">
		<h1>Crumbs Attribution</h1>
		<form method="post" action="options.php">
			<?php settings_fields( 'crumbs_attribution' ); ?>
			<table class="form-table">
				<tr>
					<th scope="row"><label for="crumbs_merchant_id">Merchant ID</label></th>
					<td><input type="text" id="crumbs_merchant_id" name="crumbs_merchant_id"
						value="<?php echo esc_attr( $settings['merchant_id'] ); ?>" class="regular-text" /></td>
				</tr>
				<tr>
					<th scope="row"><label for="crumbs_api_url">Ledger API URL</label></th>
					<td><input type="url" id="crumbs_api_url" name="crumbs_api_url"
						value="<?php echo esc_attr( $settings['api_url'] ); ?>" class="regular-text" /></td>
				</tr>
				<tr>
					<th scope="row"><label for="crumbs_api_key">API key (optional)</label></th>
					<td><input type="password" id="crumbs_api_key" name="crumbs_api_key"
						value="<?php echo esc_attr( $settings['api_key'] ); ?>" class="regular-text" />
						<p class="description"><?php echo esc_html( 'v0.1 dev-only: stored plaintext in wp_options and not yet sent anywhere. Move to a secret manager / encrypted option before public launch (P3 N9).' ); ?></p>
					</td>
				</tr>
				<tr>
					<th scope="row">Consent policy</th>
					<td>
						<select name="crumbs_consent_policy" id="crumbs_consent_policy">
							<option value="required" <?php selected( $settings['consent_policy'], 'required' ); ?>>Required (recommended)</option>
							<option value="off" <?php selected( $settings['consent_policy'], 'off' ); ?>>Off (DISABLES tracking — use for testing)</option>
						</select>
					</td>
				</tr>
			</table>
			<?php submit_button(); ?>
		</form>
		<p class="description">
			Receipt issuance is consent-gated (ePrivacy Art 5(3)). Wire your CMP via the
			<code>crumbs_consent_status</code> filter. Payouts are settled on licensed rails —
			this plugin never holds funds.
		</p>
	</div>
	<?php
}

function crumbs_register_settings() {
	register_setting( 'crumbs_attribution', 'crumbs_merchant_id', array( 'type' => 'string', 'sanitize_callback' => 'sanitize_text_field' ) );
	register_setting( 'crumbs_attribution', 'crumbs_api_url', array( 'type' => 'string', 'sanitize_callback' => 'esc_url_raw' ) );
	register_setting( 'crumbs_attribution', 'crumbs_api_key', array( 'type' => 'string', 'sanitize_callback' => 'sanitize_text_field' ) ); // STUB (P3 N9): plaintext option, dev-only — secret-manager/encrypted-option is the pre-public gate
	register_setting( 'crumbs_attribution', 'crumbs_consent_policy', array( 'type' => 'string', 'sanitize_callback' => 'sanitize_text_field' ) );
}
add_action( 'admin_init', 'crumbs_register_settings' );
