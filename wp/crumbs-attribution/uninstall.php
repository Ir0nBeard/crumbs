<?php
/**
 * Uninstall handler — remove plugin options (no orphaned data).
 *
 * @package CrumbsAttribution
 */

if ( ! defined( 'WP_UNINSTALL_PLUGIN' ) ) {
	exit;
}

delete_option( 'crumbs_merchant_id' );
delete_option( 'crumbs_api_url' );
delete_option( 'crumbs_api_key' );
delete_option( 'crumbs_consent_policy' );
