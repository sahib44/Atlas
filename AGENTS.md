# MIST Deck development

## Coordination and authorization

- The primary Codex session coordinates delegation, integration, and final verification. Handle small changes directly when that is simpler. For substantial features, UI redesigns, backend changes, or permission-sensitive work, delegate implementation, testing, and independent review as appropriate. Run independent tasks in parallel.
- Assign one owner to each writable file at a time. Give specialists the base revision, acceptance criteria, and file scope. Transfer ownership explicitly before another agent edits a file. Use separate Git worktrees and branches as needed for concurrent edits. The coordinator reconciles patches and preserves unrelated local changes.
- Get the user's approval on the mockup before implementing a UI redesign. Only the coordinator may commit or push, and only after explicit authorization for that action. Real email requires explicit user authorization. Development and testing use the existing mock provider.
- Each specialist reports work completed, files changed or inspected, tests performed or skipped, issues or risks, and anything requiring coordinator attention. The reviewer inspects the diff and preview without editing application files. Codex specialists inherit the parent session's permissions. The review role's instructions are a working agreement. For enforced read-only access, run the review in a separate read-only session.

## Application conventions

- The React, TypeScript, Vite frontend lives in `apps/web`. Routes and shared navigation are in `src/App.tsx`. Use the existing React Query API hooks, shared controls in `ui.tsx`, and light/dark design tokens in `style.css`. Keep layouts responsive and controls accessible by keyboard.
- Write interface copy for the team using the app. Avoid marketing language and three-phrase subtitles. Add supporting text only when it provides information needed to use the page.
- The FastAPI application, SQLAlchemy models and PostgreSQL worker live in `apps/api/mist`. Feature routers are registered in `main.py`. Alembic revisions belong in `apps/api/migrations/versions`. Keep the model and migration in sync, preserve existing IDs and history, and use additive migrations for existing data.
- Sessions use HTTP-only cookies. State-changing authenticated requests require `X-MIST-Request: 1`. Enforce permissions in the API using `auth.py` and mirror action visibility in the frontend. Audit sensitive state transitions and keep related writes in one transaction.
- Competition records belong to a tournament by `event_year`. Keep recruitment responses separate from the official assignment ledger. Use the assignment service and its transaction lock when changing assignments, merging contacts, or clearing judge records.
- Campaigns, conversations, notifications and the PostgreSQL outbox are shared infrastructure. The worker uses a mock provider with no network email transport. Keep real records, secrets, database files and generated artifacts out of Git.

## Test environments and verification

- Start the local stack with `docker compose up --build -d` from the repository root after configuring `.env` from `.env.example`. The web preview is on port 5180 by default. Run `docker compose --profile test run --build --rm test` for backend tests. From `apps/web`, run `npm ci`, `npm run build` and `npm test` with Node 22.
- Run `./scripts/e2e.sh` from the root for Playwright against the disposable `mist_e2e` stack. Use disposable data and the mock provider for tests and keep the working app on port 5180 and its database outside test runs. The E2E runner resets a fixed Compose project and backend tests truncate `mist_test`. Give each shared test stack one runner at a time. Parallel stacks need separate Compose projects, database volumes, and host ports.
- Cover changes to permissions, migrations, data integrity and user-facing workflows with the relevant tests. The coordinator integrates fixes and verifies the final combined diff with the relevant checks and a local preview. Compare UI redesigns against the approved mockup.
