# Echo devices missing after successful Amazon connection

Amazon login could succeed through the amazon.com fallback while later device and control requests still used the constructor's amazon.in default. Device requests on the configured marketplace redirected to the Amazon sign-in page, so parsing returned no devices. This affected discovery before the skill's customer-profile permission was involved.

The per-login adapter now retains the marketplace of the authenticated customer response after session confirmation and customer-ID verification. Later AlexaPy device/control requests use that account's confirmed marketplace. The server-wide default is unchanged; another user's session retains its own marketplace. Restoration repeats confirmation, so existing saved sessions adopt the correct marketplace without requiring a new password or OTP or a new metadata file.

Validation:

- 37 focused regression cases passed, covering confirmation, marketplace fallback, OTP/cookie promotion, session restoration, redirect rules, cancellation, canonical identity and device presence.
- 18 confirmation/session/redirect cases passed against the deployed AlexaPy 1.34.0 runtime. The fallback test also invokes the real AlexaPy device API builder and asserts that its URL uses the confirmed marketplace, while another login remains on its own original marketplace.
- Read-only checks with a private temporary copy of the saved cookies reproduced the regional sign-in redirect. With the updated adapter, cookie-only session confirmation selected amazon.com and a device GET returned HTTP 200 with music-capable devices. No OAuth refresh, password/OTP submission or playback was performed during that probe. Customer emails, names, serials and cookies were not printed or published.

After deployment, refresh/reopen the audio-output list. Device discovery should use the existing saved session. Android source and API response formats are unchanged; no Android compilation is required.
