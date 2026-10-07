# User management

Only an Admin can open **Settings → Users** or call the user-management API.

## Invite a user

1. Open **Settings → Users** and select **Invite User**.
2. Enter the normalized email address and choose Admin, Ops, or Viewer.
3. Select **Create Invite**.
4. Copy the displayed link and share it securely with the intended recipient.

ClueCDC does not send email in v0.2. The link contains a random token; the
database stores only its SHA-256 hash. An invite expires after
`INVITE_TTL_SECONDS` (24 hours by default), can be accepted once, and is replaced
when an Admin issues a new invite for the same pending account.

The recipient opens `/invite/<token>`, verifies the displayed email, and chooses
a password of at least 12 characters. Successful activation marks the account
Active. Expired, replaced, and already-used links all show the same invalid
invite message.

## Change access or disable an account

Use the Role selector in the user table to change an account among Admin, Ops,
and Viewer. Use **Disable** to prevent login and invalidate existing sessions.
Use **Enable** to restore an account that already has a password. An invited
account without a password remains Invited until its invite is accepted.

An administrator cannot disable their own current account. Keep at least one
working Admin account; ClueCDC does not ship recovery credentials.

User invitations, activations, status changes, role changes, login outcomes,
and logout are written to the existing audit log. Passwords, password hashes,
raw session values, and raw invite tokens are never included.
