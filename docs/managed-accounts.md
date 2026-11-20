# Managed Atlas accounts

Atlas has two account modes. The default `sandbox` mode is for local demos and
regression tests. Set `MIST_ACCOUNT_MODE=managed` for an invitation-only workspace.
Managed mode skips demo seeding and accepts only managed identities. Existing
sandbox accounts, sessions, and their historical references stay in the database
and cannot authenticate in managed mode. Use a separate database for each environment.

## Bootstrap the Competitions Lead

1. Configure a private Atlas database, apply the Alembic migrations, and set
   `MIST_ACCOUNT_MODE=managed`. Set `MIST_PUBLIC_ORIGIN` to the canonical HTTPS
   origin. Generate a distinct random token key with Python's
   `secrets.token_urlsafe(48)` and supply it as `MIST_ACCOUNT_TOKEN_KEY` to both
   API and worker through the deployment secret store. Managed session cookies
   are always Secure, HttpOnly, and SameSite=Strict.
2. In a trusted operator terminal run the bootstrap. Use the Lead's individual
   MIST mailbox. The command prompts for the password without echoing it.

   ```sh
   docker compose exec api python -m mist.accounts bootstrap \
     --email lead-placeholder@getmistified.com --name "Competitions Lead"
   ```

   Bootstrap refuses a second managed Lead. The Lead uses the existing
   `competitions_chair` role and retains its action permissions. Heads use
   `competitions_member`. Keep the bootstrap operator and database credentials
   separate from ordinary Atlas accounts.
3. Sign in as the Lead and open **User Management**. An invitation reserves one
   position for one address. Deactivation keeps the reservation; reactivation
   requires a fresh sign-in. Revocation releases the position for a replacement.
   Moving an account ends the old assignment and creates a historical successor.
   A replacement must use a distinct individual address. Once an address has
   an account record, Atlas does not transfer that identity to a new person.
   Only the Lead can perform these actions. Heads can work in any department.

A fresh bootstrap initializes the built-in permanent 2027 catalog. Existing
competition records are preserved. The four existing sports competitions appear
under their respective heads on their home dashboards while remaining in the
shared Sports Referees directory. Classification of future sports pilots is a
separate catalog decision.

## Invitation and reset delivery

The current provider is the mock worker. It records delivery receipts without
sending external email. Authentication messages use the existing retry queue,
with generated links available to the provider only in memory. They are outside
the shared Outbox. The Lead can resend an invitation without seeing its token.
Password reset requests use the same generic response for known and unknown
addresses. The worker checks account eligibility from a bounded, temporary
queue and applies a five-minute cooldown per eligible account. Completing a
password reset invalidates existing Atlas sessions.

For isolated QA only, set `MIST_ALLOW_TEST_ACCOUNT_LINKS=true` and use the
operator-only `python -m mist.accounts sandbox-link --message-id UUID` when
testing with a fabricated recipient. Treat the printed link as a credential.
Leave this setting off in shared or production environments.

Before onboarding real staff, configure an authorized email provider for invite
and reset messages, verify delivery, and keep bearer links and message bodies out
of provider logs. Use the same signing key across API and worker instances.
Coordinate key rotation with expiry or revocation and reissue links as needed.

## Cloudflare Access

1. Create a self-hosted Access application for the entire Atlas HTTPS hostname,
   including `/api`, `/activate`, and `/reset-password`. Set an Allow policy
   with individual MIST addresses and use the email one-time PIN identity
   provider. Add the intended recipient before sending an Atlas invitation.
   If Access sign-in interrupts the activation link, finish Access login first
   and reopen the original email link.
2. Route the hostname through Cloudflare Tunnel to the private web service.
   Enable **Protect with Access** in `cloudflared` with the correct team and
   application audience so the connector validates the Access JWT. Keep the
   web origin, API, worker, and database off public interfaces. Confirm direct
   origin connections fail and the full hostname is covered by Access. Atlas
   ignores client-supplied identity headers and always requires its own session.
3. For offboarding, revoke the Atlas assignment, remove the individual's address
   from the Access policy, and revoke the affected Access sessions. For
   temporary suspensions, choose whether to remove edge access for the duration.
   Reconcile the allowlist against current Atlas assignments and pending invitations.
4. Apply edge rate limits to Atlas sign-in and token endpoints, and rehearse
   onboarding and offboarding with test identities. Preserve Atlas database
   backups and monitor the worker queue.

Cloudflare supports changing an application's Access policy through its API and
revoking user tokens separately. Automatic synchronization would require a
separately scoped credential, retry and drift reconciliation, and decisions
about failure handling across the two systems. Use the manual procedure for this
phase. No Cloudflare credentials or automation are part of this repository.

Official Cloudflare references

- [One-time PIN login](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/)
- [Self-hosted applications](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)
- [Tunnel origin parameters and Protect with Access](https://developers.cloudflare.com/tunnel/reference/origin-parameters/)
- [Update an Access application policy](https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/subresources/policies/methods/update/)
- [Session management and revocation](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/session-management/)
