# Music Box design system

Use the existing graphite and orange Music Box identity across the landing page,
web player, account screens and owner panel.

## Tokens

`flask-server/templates/static/css/theme.css` is the shared source for colours,
fonts, surfaces, spacing and semantic states. Reuse these names; do not create
private palettes. `account-design.css` defines account geometry and the shared
48px form controls. Keep square corners consistent with the web player. `landing.css` defines the public welcome composition.

## Surfaces

- Public welcome: left-aligned typography, concise product facts, real login links
- Account: identity summary, section navigation, grouped settings, focused dialogs
- Owner panel: persistent navigation, readable user rows, consistent actions
- Android: native theme tokens, grouped settings, one service row per provider

## Authentication

Show a single verification or editing step at a time. Server verification gates
progression. Final mutations recheck proofs and consume codes once. Use absolute
server deadlines for email-code expiry and resend countdowns. Mask no errors
behind hidden steps. Clear password and code fields when a dialog closes.

Email replacement signs out all sessions after the new address is confirmed;
show a clear sign-in handoff. Keep the existing authenticator active until its
replacement is confirmed. Only owners can use the management panel.

## Controls and accessibility

Keep labels in sentence case, instant keyboard focus rings, explicit loading and
error states, visible password toggles and reduced-motion support. Buttons on
orange use dark text to preserve contrast. Respect both dark and light themes.

Avoid decorative gradients, icon tiles, invented metrics and filler sections.
