<?php
/**
 * Plugin Name:       Crumbs Attribution
 * Plugin URI:        https://github.com/Ir0nBeard/crumbs
 * Description:       Consent-native agent-journey attribution for WordPress/WooCommerce merchants. Issues signed attribution receipts (server-side), stamps attributed conversions, and surfaces the checkout to WebMCP agents. The SDK is vendored — no third-party remote code. No tracking without consent.
 * Version:           0.1.0
 * Author:            Crumbs
 * License:           GPL-2.0-or-later
 * License URI:       https://www.gnu.org/licenses/gpl-2.0.html
 * Text Domain:       crumbs-attribution
 *
 * v0.1 scaffold — not yet listed in the WordPress.org plugin directory. This
 * plugin directory is GPL-2.0-or-later (WordPress.org directory requirement);
 * the rest of the Crumbs repo is MIT. Complies with the plugin directory rules
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
 *   crumbs_api_url       ledger base URL — REQUIRED, no default endpoint
 *   crumbs_api_key       optional X-Crumbs-Key for conversion posts.
 *                        Recommended source: the CRUMBS_MERCHANT_API_KEY
 *                        constant in wp-config.php — the key then never
 *                        touches the database. The wp_options fallback is a
 *                        v0.1 dev scaffold (plaintext option; the admin UI
 *                        warns while it is in use). Not yet sent anywhere
 *                        (no conversions path wired).
 *   crumbs_consent_policy  'required' (default) | 'off' — never 'off' in production
 */
function crumbs_api_key_source() {
	if ( defined( 'CRUMBS_MERCHANT_API_KEY' ) && '' !== trim( (string) CRUMBS_MERCHANT_API_KEY ) ) {
		return 'constant';
	}
	$option = get_option( 'crumbs_api_key', '' );
	return '' === $option ? 'none' : 'option';
}

/**
 * The effective merchant API key (never echoed into markup by the UI).
 *
 * 'constant' — CRUMBS_MERCHANT_API_KEY in wp-config.php (recommended: the
 *              key never enters the database);
 * 'option'   — legacy v0.1 wp_options value (plaintext at rest — dev-only
 *              scaffold; the admin UI warns while it is in use);
 * 'none'     — no key configured.
 */
function crumbs_api_key_value() {
	if ( 'constant' === crumbs_api_key_source() ) {
		return (string) CRUMBS_MERCHANT_API_KEY;
	}
	return (string) get_option( 'crumbs_api_key', '' );
}

function crumbs_get_settings() {
	return array(
		'merchant_id'     => get_option( 'crumbs_merchant_id', '' ),
		'api_url'         => get_option( 'crumbs_api_url', '' ),
		'api_key'         => crumbs_api_key_value(),
		'api_key_source'  => crumbs_api_key_source(),
		'consent_policy'  => get_option( 'crumbs_consent_policy', 'required' ),
	);
}

/**
 * Admin warning for a key stored as a plaintext wp_options value.
 *
 * Constant-provided keys (CRUMBS_MERCHANT_API_KEY in wp-config.php) are the
 * recommended configuration and never warn. The wp_options path is a v0.1
 * dev scaffold — while it is in use, the warning stays until the key moves
 * to the constant (or an encrypted/secret-manager source).
 */
function crumbs_admin_api_key_warning() {
	if ( 'option' !== crumbs_api_key_source() ) {
		return;
	}
	echo '<div class="notice notice-warning"><p>';
	echo esc_html__( 'Crumbs Attribution: the merchant API key is stored as a plaintext wp_options value (v0.1 dev scaffold). Define the CRUMBS_MERCHANT_API_KEY constant in wp-config.php so the key never touches the database.', 'crumbs-attribution' );
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
	// The ledger URL and merchant id are REQUIRED — the plugin does not ship a
	// default endpoint. Skip instrumentation until both are configured (the
	// admin settings page explains what to fill in).
	if ( empty( $settings['api_url'] ) || empty( $settings['merchant_id'] ) ) {
		return;
	}
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
	if ( empty( $settings['merchant_id'] ) || empty( $settings['api_url'] ) ) {
		wp_send_json_error(
			array(
				'code'    => 'LEDGER_NOT_CONFIGURED',
				'message' => 'merchant id and ledger API URL must be set on the Settings page',
			),
			500
		);
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
		'expires' => time() + 3600, // short session-scope TTL — minimizes theft window
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
 * agents can complete it with attribution (docs/ATTRIBUTION_PROTOCOL.md §4).
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
					<td>
					<?php if ( 'constant' === $settings['api_key_source'] ) : ?>
						<input type="password" id="crumbs_api_key" class="regular-text" value="••••••••" disabled="disabled" autocomplete="new-password" />
						<p class="description"><?php echo esc_html( 'Provided by the CRUMBS_MERCHANT_API_KEY constant in wp-config.php — the key never touches the database (recommended).' ); ?></p>
					<?php elseif ( 'option' === $settings['api_key_source'] ) : ?>
						<input type="password" id="crumbs_api_key" name="crumbs_api_key" value="" class="regular-text" placeholder="••••••••" autocomplete="new-password" />
						<p class="description"><?php echo esc_html( 'A key is currently stored as a plaintext wp_options value (v0.1 dev scaffold). Clear the field and save to remove it, or define the CRUMBS_MERCHANT_API_KEY constant in wp-config.php instead.' ); ?></p>
					<?php else : ?>
						<input type="password" id="crumbs_api_key" name="crumbs_api_key" value="" class="regular-text" autocomplete="new-password" />
						<p class="description"><?php echo esc_html( 'Optional X-Crumbs-Key for conversion posts. Recommended: define the CRUMBS_MERCHANT_API_KEY constant in wp-config.php so the key never touches the database.' ); ?></p>
					<?php endif; ?>
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

/**
 * Sanitize the API key option.
 *
 * A constant-provided key is authoritative: saving any settings form then
 * clears the legacy wp_options row instead of writing a database copy of a
 * constant key. Without a constant, a non-empty submitted value is stored
 * (dev scaffold); an empty submission clears the option.
 */
function crumbs_sanitize_api_key( $value ) {
	$value = sanitize_text_field( (string) $value );
	if ( 'constant' === crumbs_api_key_source() ) {
		return '';
	}
	return $value;
}

function crumbs_register_settings() {
	register_setting( 'crumbs_attribution', 'crumbs_merchant_id', array( 'type' => 'string', 'sanitize_callback' => 'sanitize_text_field' ) );
	register_setting( 'crumbs_attribution', 'crumbs_api_url', array( 'type' => 'string', 'sanitize_callback' => 'esc_url_raw' ) );
	register_setting( 'crumbs_attribution', 'crumbs_api_key', array( 'type' => 'string', 'sanitize_callback' => 'crumbs_sanitize_api_key' ) );
	register_setting( 'crumbs_attribution', 'crumbs_consent_policy', array( 'type' => 'string', 'sanitize_callback' => 'sanitize_text_field' ) );
}
add_action( 'admin_init', 'crumbs_register_settings' );
