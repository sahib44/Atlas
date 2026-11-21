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
   API and worker through the deployment secret store. Generate a second key
   the same way for `MIST_RECOVERY_KEY` and back it up separately from the
   database. This key protects recovery destinations and must stay consistent
   across processes and restarts. Managed session cookies are always Secure,
   HttpOnly, and SameSite=Strict.
2. Set `MIST_COMPETITIONS_LEAD_EMAIL` to the official shared Competitions
   mailbox when the organization is ready to use it. In a trusted operator
   terminal, run the bootstrap with that address. The command prompts for a
   dedicated Atlas password without echoing it. Access to the shared mailbox
   alone does not grant Lead privileges.

   ```sh
   docker compose exec api python -m mist.accounts bootstrap \
     --email competitions-placeholder@getmistified.com --name "Competitions Lead"
   ```

   Bootstrap refuses a second managed Lead. The Lead uses the existing
   `competitions_chair` role and retains its action permissions. Heads use
   `competitions_member`. Keep the bootstrap operator and database credentials
   separate from ordinary Atlas accounts.
3. Sign in as the Lead and verify a personal backup method. Then open
   **User Management**. An invitation reserves one
   position for one address. Deactivation keeps the reservation; reactivation
   requires a fresh sign-in. Revocation releases the position for a replacement.
   Moving an account ends the old assignment and creates a historical successor.
   A replacement must use a distinct individual address. Once an address has
   an account record, Atlas does not transfer that identity to a new person.
   Only the Lead can perform these actions. Heads can work across competitions.

A fresh bootstrap initializes the built-in permanent 2027 catalog. Existing
competition records are preserved. The four existing sports competitions appear
under their respective heads on their home dashboards while remaining in the
shared Sports Referees directory. Classification of future sports pilots is a
separate catalog decision.

## Recovery methods and Lead handover

Managed accounts need one verified backup before opening the workspace.
Existing account passwords and assignments are preserved. A Lead can verify
an external personal email or an individual MIST address. A head can verify an
external personal email. Both roles can use a personal phone when an SMS
provider is available. The login address is always ineligible as a backup.
Select a separate mailbox that you control. Atlas cannot inspect mail-routing
rules to identify aliases or external forwarding to a shared inbox.

Use **Profile → Recovery methods** to view masked destinations, add and verify
a method, select a preferred method, or remove a method. Enter your current
password when changing methods. The last verified method cannot be removed.
Use an eligible email during enrollment if SMS is unavailable.

Forgot password asks for the sign-in address and the exact backup destination
to use. The form shows the same channel choices for everyone, confirms the
request generically, and only delivers to a verified matching destination.
Completing a reset revokes active sessions and prior reset links. The shared
Competitions mailbox never receives a password-reset link.

To hand over the single Lead account, back up the database and recovery key,
confirm the intended organizational mailbox, and run the following from a
trusted operator terminal.

```sh
docker compose exec api python -m mist.accounts transition-lead
```

The incoming Lead sets a new Atlas password. The procedure keeps the Lead's
user ID and existing competition and audit history, invalidates existing
sessions and tokens, and removes the outgoing Lead's backup methods. The
incoming Lead signs in and verifies a new backup before accessing the
workspace. Handle a lost password with no working verified destination through
a verified operator handover for the Lead. Individual head accounts without a
usable backup require a recovery policy decision. The Lead can revoke access,
and a replacement account requires a distinct individual address.

## Invitation and reset delivery

The current provider is the mock worker. It records delivery receipts without
sending external email. Authentication messages use the existing retry queue,
with generated links available to the provider only in memory. They are outside
the shared Outbox. The Lead can resend an invitation without seeing its token.
The worker checks account eligibility from a bounded, temporary queue and
applies a five-minute cooldown per eligible account. Recovery destinations are
encrypted at rest and masked in the application. Tokens and unmasked
destinations must stay out of provider logs and audit payloads.

For isolated QA only, set `MIST_ALLOW_TEST_ACCOUNT_LINKS=true` and use the
operator-only `python -m mist.accounts sandbox-link --message-id UUID` when
testing with a fabricated recipient. Treat the printed link as a credential.
Leave this setting off in shared or production environments.

Before onboarding real staff, configure an authorized email provider for invite
and recovery messages and verify delivery. Use the same signing and recovery
keys across API and worker instances. Rotating the signing key requires
reissuing outstanding links. Changing the recovery key requires a deliberate
re-encryption and lookup migration. There is no configured SMS transport.
The worker has an SMS provider interface for a future adapter. Automated
tests inject a fake adapter. The running interface marks phone recovery
unavailable until a working adapter is configured.

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
