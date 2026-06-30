<?php
/**
 * Plugin Name: PST Sync Bridge
 * Description: Exposes the phone usermeta (the sync join key) and a server-managed
 *              modified timestamp to the WP REST API, so the Psychology Test Platform
 *              can sync users bidirectionally over REST (no MySQL access required).
 * Author:      Psychology Test Platform
 *
 * ── Install ────────────────────────────────────────────────────────────────────
 * Drop this file into  wp-content/mu-plugins/pst-sync.php  (create the folder if it
 * doesn't exist). Must-use plugins activate automatically and cannot be disabled
 * from the dashboard — exactly what we want for a sync invariant.
 *
 * ── What it exposes ────────────────────────────────────────────────────────────
 *  • PST_PHONE_KEY   (default 'billing_phone')      → readable AND writable in REST
 *      edit context, so the app reads the phone (join key) and sets it on the users
 *      it creates/updates. Must equal WP_PHONE_META_KEY on the app side.
 *  • PST_MODIFIED_KEY (default 'pst_modified_gmt')  → readable in REST edit context,
 *      server-managed (NOT client-writable). Bumped on user_register, profile_update
 *      and any usermeta change. Powers last-modified-wins / edit detection. Must
 *      equal WP_MODIFIED_META_KEY on the app side.
 *
 * Override the keys via the `pst_sync_phone_key` / `pst_sync_modified_key` filters
 * if your app config uses different meta keys.
 *
 * ── Least-privilege sync account ───────────────────────────────────────────────
 * The app authenticates with an Application Password for a dedicated WP account
 * (e.g. `sync-bot`). To read users with context=edit and to create/update users it
 * needs the caps `list_users`, `create_users`, `edit_users` (and `promote_users` to
 * assign a role). The simplest grant is the Administrator role; for tighter scope,
 * create a custom role with exactly those four caps and assign the sync account to
 * it. Never reuse a human admin account; rotate the Application Password as a secret.
 */

if (!defined('ABSPATH')) {
    exit; // no direct access
}

if (!function_exists('pst_sync_phone_key')) {
    function pst_sync_phone_key() {
        return apply_filters('pst_sync_phone_key', 'billing_phone');
    }
    function pst_sync_modified_key() {
        return apply_filters('pst_sync_modified_key', 'pst_modified_gmt');
    }
}

/**
 * Register both usermeta keys for REST. `show_in_rest` surfaces them in the user's
 * `meta` object under context=edit.
 */
add_action('init', function () {
    // Phone — the join key. Writable over REST by anyone who can edit users.
    register_meta('user', pst_sync_phone_key(), array(
        'type'              => 'string',
        'single'            => true,
        'show_in_rest'      => true,
        'sanitize_callback' => 'sanitize_text_field',
        'auth_callback'     => function ($allowed, $meta_key, $user_id) {
            return current_user_can('edit_user', $user_id);
        },
    ));

    // Modified timestamp — readable over REST, but server-managed: reject REST
    // writes (auth_callback false) while still allowing direct update_user_meta().
    register_meta('user', pst_sync_modified_key(), array(
        'type'          => 'string',
        'single'        => true,
        'show_in_rest'  => true,
        'auth_callback' => '__return_false',
    ));
});

/**
 * Stamp the modified timestamp (GMT, ISO-8601) whenever a user or their meta changes.
 * Guarded against recursion: changes to our own timestamp key are ignored.
 */
if (!function_exists('pst_sync_touch_user')) {
    function pst_sync_touch_user($user_id) {
        if (!$user_id) {
            return;
        }
        update_user_meta($user_id, pst_sync_modified_key(), gmdate('c'));
    }
}

add_action('user_register', 'pst_sync_touch_user', 10, 1);
add_action('profile_update', 'pst_sync_touch_user', 10, 1);

// Any usermeta add/update/delete (except our own key) bumps the timestamp.
foreach (array('added_user_meta', 'updated_user_meta', 'deleted_user_meta') as $hook) {
    add_action($hook, function ($meta_id, $user_id, $meta_key) {
        if ($meta_key === pst_sync_modified_key()) {
            return; // avoid infinite recursion on our own write
        }
        pst_sync_touch_user($user_id);
    }, 10, 3);
}
