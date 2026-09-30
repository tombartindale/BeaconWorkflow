# Deployment specification

# Cloud-deployable BeaconWorkflow

## Context

BeaconWorkflow (bcn/ui/ tooling) is currently a **single-user, single-machine, localhost-only** desktop-style app: a Fastify server (`ui/server`) spawns the Python `bcn` CLI (`tooling/bcn`) as one-shot subprocesses, streams progress over SSE, and reads/writes a "programme root" that lives on local disk (an actual folder tree of `programme.toml` / module / unit / topic files, media, and build artifacts). There is no auth, no database beyond a local SQLite job-history file, no container images, and no CI/CD.

The goal is to make this deployable for a small team (2-5 concurrent users, occasional overlap), with long-running "activities" (render, compose, bumpers, subtitles, package, qa — real ffmpeg/Marp/Chrome jobs taking seconds to minutes) handled by a proper queue/worker mechanism instead of in-process `child_process.spawn`.

**Key decision, made explicitly after an earlier draft of this plan over-committed to Azure-specific services up front**: design and build this as a **provider-agnostic Docker Compose stack first** — plain containers, Postgres, a queue, a shared volume, a reverse proxy — that runs identically on a laptop, a DigitalOcean droplet, or an Azure VM. Defer picking Azure vs. DigitalOcean (and their managed-service upgrades) until the stack works and real constraints (cost, existing tenancy, team familiarity) are known. This is a large simplification over the original draft: it replaces two parallel "how this looks on Azure" / "how this looks on DigitalOcean" architectures with **one stack plus a short mapping of where each piece could later move** if a managed service is wanted.

## What has to fundamentally change vs. the current design

1. **Filesystem-as-database.** The entire pipeline's state (topic markdown, review decisions, edit videos, build artifacts, delivery packages) is a folder tree read/written via plain file I/O with mtime-based freshness checks and atomic renames. `bcn` itself has zero cloud-storage awareness.
2. **In-process job queue.** `JobQueue` in `ui/server/src/jobs.ts` is a `Map` in one Node process, backed by SQLite only for crash recovery of *that same process*. Concurrency control (`overlaps()` — never run two jobs on overlapping paths) is enforced in-memory. This cannot span multiple server instances.
3. **In-process pub/sub for progress.** The `Bus` that fans job progress out to SSE clients is purely in-memory; SSE connections are pinned to one backend process.
4. **No auth, localhost-only.** `server.ts` actively 403s any request whose Host/Origin isn't localhost. There's no login, no per-user identity beyond a cosmetic "operator" label.
5. **Local subprocess dependencies.** `bcn` shells out to a pinned ffmpeg 7.1, Node 22, Marp, and Chrome for Testing — heavy, slow, CPU-bound work that today runs colocated with the API server.

The plan below picks the smallest architecture that fixes all five while reusing everything else as-is. The `bcn` CLI's contract — one-shot process, JSON envelope on stdout, NDJSON progress on stderr — does not need to change at all; it's already a queue-friendly design.

## The stack (provider-agnostic, this is the actual deliverable)

```
docker-compose.yml
├── proxy        reverse proxy / TLS termination / basic auth (Caddy or Traefik)
├── api          Fastify (ui/server), stateless, can run >1 replica
├── worker       same image as api, runs jobs.ts's invoke()/run() logic, pulls from queue
├── postgres     replaces SQLite: jobs, prefs, (later: programmes, members, settings)
├── redis        queue (BullMQ) + pub/sub (job progress fanout) — one small, well-understood service
└── volume: programme-root   shared bind mount / named volume, holds the programme root(s)
```

- **Everything here is a plain container or a well-known open-source service** (Postgres, Redis) — nothing provider-specific. This is exactly what runs on a laptop with `docker compose up`, and exactly what a single DigitalOcean droplet or Azure VM can run unchanged via `docker compose up -d`.
- **Why Redis over a cloud-specific queue**: BullMQ (Redis-backed) gives retries/backoff/visibility timeouts for free, is a single well-trodden dependency, and — critically — is identical in dev, on a droplet, or behind a managed Redis instance later (Azure Cache for Redis / DigitalOcean Managed Redis) with zero application code changes, just a connection string. This replaces an earlier draft's Azure Storage Queue choice, which only existed to be "Azure-native" and would have meant different queue code for each provider.
- **The `overlaps()` "don't touch the same topic twice" rule** (`jobs.ts:49-54`) becomes an advisory lock taken at dequeue time (a Redis key with a TTL, or a row lock in Postgres) instead of an in-memory check in the `JobQueue` class — same logic, centralized so it works across worker replicas.
- **Worker** is almost a direct lift of the existing `invoke()`/`run()` logic in `jobs.ts:245-309`: dequeue a job, `spawn()` `bcn` exactly as today, relay NDJSON stderr lines to Redis pub/sub keyed by job id, write the final envelope to Postgres. The only change from today's code is *where the job comes from* (dequeue, not `this.jobs`) and *where progress goes* (Redis channel, not the in-process `Bus`).
- **`bus.ts`** swaps its in-process event emitter for a thin Redis pub/sub subscription — a small, contained change. API replicas subscribe and re-broadcast to their own connected SSE clients, so SSE keeps working even with more than one API replica.
- **Storage**: the programme root stays exactly what it is today — a folder tree — mounted as a Docker volume shared between `api` and `worker`. Locally and on a single droplet/VM this is just a bind mount; this is deliberately the one piece flagged below as provider-dependent once you go multi-host (see "Where this maps later").
- **Auth**: email-based passwordless login ("magic link" — enter an email, get a one-time sign-in link), restricted to a domain allowlist (e.g. `@northumbria.ac.uk`). This is the primary auth mechanism, not a placeholder — decided after confirming that registering an Entra ID app (needed for "Sign in with Microsoft") requires tenant-admin approval at Northumbria, which is an external dependency outside this project's control. Magic-link login needs no admin approval from anyone: it's app code (a `login_tokens` table in Postgres — email, token, expiry — an SMTP/transactional-email send via any provider, e.g. Resend/SES/Mailgun — and a domain check against an allowlist config value) plus TLS from the reverse proxy, and it gives real per-user identity (an email address) that the ACL/membership system below can key on directly, unlike shared basic auth. Entra ID / Azure AD login remains available as a **later upgrade**, if and when university IT grants the app-registration approval — swapping the login mechanism doesn't touch the ACL/membership design at all, since both ultimately resolve to "an email address the app trusts."
- **Container image**: one Dockerfile, layering ffmpeg 7.1, Node 22, Python + `bcn`'s venv, Marp, and Chrome for Testing — today's `scripts/setup.sh` documents exactly what needs to go in it. Same image serves as both `api` and `worker` (worker just runs a different entrypoint/command).

## Suggested build order

1. **Containerize**: write the Dockerfile, get `bcn` + `ui/server` running in a container against a bind-mounted `example/` programme root via `docker compose up` — proves the image before anything else changes.
2. **Externalize state**: swap SQLite (`db.ts`) for Postgres (a local container); mechanical change since better-sqlite3 usage is already isolated to `db.ts`.
3. **Split the job runner into `worker`**: extract `jobs.ts`'s `invoke()`/`run()` logic into a standalone entrypoint that pulls from a Redis/BullMQ queue instead of `this.jobs`; the API's `submit()` becomes "enqueue + insert a queued row."
4. **Swap the `Bus`** for Redis pub/sub so SSE works across API replicas.
5. **Add the reverse proxy** (Caddy/Traefik, for TLS) and the app-level email magic-link login with the Northumbria domain allowlist, and relax the current localhost-only Host/Origin check in `server.ts` accordingly.
6. **Wire up CI**: GitHub Actions building the image and pushing to a registry (GitHub Container Registry works for either provider) — deployment itself stays a `docker compose pull && up -d` on whatever host you land on, so this step doesn't lock in a provider either.

At the end of this, you have a fully working, multi-user, queue-backed deployment you can run anywhere Docker runs — a single droplet, a single Azure VM, or even a beefy machine you already own — before spending any time on managed-service specifics.

## Where this maps later, once a provider/constraints are known

This section is intentionally short — it's a reference for *when* you decide to move off "one host running Docker Compose" to managed services, not something to act on now.

| Stack piece | Docker Compose (now) | Azure upgrade path | DigitalOcean upgrade path |
|---|---|---|---|
| Compute | `docker compose` on one host | Azure Container Apps (splits api/worker into separate scalable services) | DigitalOcean App Platform (Service + Worker components), or keep the droplet |
| Queue | Redis container | Azure Cache for Redis (same BullMQ code, new connection string) | DigitalOcean Managed Redis (same code) |
| Postgres | Postgres container | Azure Database for PostgreSQL – Flexible Server | DigitalOcean Managed PostgreSQL |
| Shared programme-root storage | Docker volume / bind mount | Azure Files, natively mountable into Container Apps — the smoothest path since it needs no code change | No native multi-attach block storage; needs either a small NFS droplet or a move to Spaces + per-job scratch sync (real new code) |
| Auth | App-level email magic-link login + domain allowlist | Same app-level login keeps working unchanged; Entra ID/Easy Auth is an optional *additional* upgrade if university IT later approves an app registration — no app-code changes needed to the ACL model either way | Same app-level login; no Azure-specific upgrade path exists here anyway |
| TLS | Caddy automatic HTTPS | Managed by Container Apps ingress | Managed by App Platform, or Caddy on the droplet |

The one genuinely load-bearing difference between the two providers is **shared storage past a single host**: Azure Files is a natural, code-free upgrade; DigitalOcean has no equivalent and needs either an extra NFS droplet or new sync code. Auth is *not* a differentiator between providers any more, since the magic-link mechanism needs no cloud-specific identity service and no admin approval from anyone outside the project — it works identically wherever the stack runs. Storage is therefore the fact that should drive the eventual provider choice, once real constraints (existing Azure tenancy/credits, team familiarity, cost) are on the table — not committed to speculatively now.

## Extension: multiple programmes with per-programme ACLs

Today one deployment = one programme (one `--root`, one `App` instance holding one DB/job-queue/status-cache/watcher, enforced by `checkRoot()` in `app.ts:27-46` requiring exactly one `programme.toml` at the root). Supporting several programmes (e.g. one per partner/course) behind a single deployment, with different people allowed into different programmes, is an additive layer on top of the stack above, not a rewrite, and doesn't depend on which provider is chosen.

### What changes conceptually

Today "the app" and "the programme" are the same object (`App`). Multi-programme support splits that into:

- **Programme registry**: a new `programmes` table (id, name, storage path/prefix, created_at, archived) in Postgres. No equivalent exists today — the set of programmes is implicit (whatever `--root` was passed at launch).
- **Per-programme isolation, same as today, just multiplied**: each programme still gets its own job queue partition, its own subtree of shared storage, its own `bcn` invocations against its own root path. The existing `App`/`JobQueue`/`Watcher`/`StatusCache` logic is reusable almost as-is *per programme* — keyed and instantiated per programme id instead of once globally. The worker's dequeue step reads `programme_id` off the job row and resolves the right root path (`<shared-volume>/<programme_id>/`) before invoking `bcn`; the `Watcher` runs one instance per active programme (or one loop iterating all of them — cheap, since it's already just a periodic directory scan).
- **Every route, job, and SSE subscription becomes programme-scoped**: `/api/status` becomes `/api/programmes/:id/status`, jobs carry a `programme_id` column, and SSE clients subscribe to a specific programme's stream.

### Auth and authorization

- **Identity**: the email magic-link login (domain-allowlisted to Northumbria, per the base stack's auth design) proves *who* someone is via their verified email address — it does not decide *which programmes* they may touch, which is a separate, app-level ACL. If Entra ID is added later, it resolves to the same thing (a verified email/object id), so the authorization layer below doesn't need to change.
- **Authorization**: a `programme_members` table (`programme_id`, `email`, `role`), managed by hand within the app. A small authorization check in Fastify's `onRequest` hook (alongside the existing Host/Origin check in `server.ts`) confirms the logged-in user's email has a row for the requested `:programmeId` before proceeding, 403ing otherwise. Two roles are enough given the current single-operator model: `member` (everything `operator` can do today: run jobs, review, edit) and `owner` (also manages membership via a small in-app admin screen) — a richer permission matrix isn't justified unless requested.
- **Frontend**: the Quasar SPA gains a programme picker (list programmes the logged-in user belongs to) as the new landing page, replacing today's single fixed root.

### Storage layout

The shared volume/file share becomes one root with a subdirectory per programme (`<volume>/<programme_id>/programme.toml`, ...) rather than the whole volume being one programme root — each worker invocation for a given job is simply pointed at its own subdirectory, so `bcn`'s existing path handling is untouched. A single shared volume (rather than fully separate volumes per programme) is simpler and cheaper to provision; isolation between programmes is enforced at the app level (a worker is only ever handed the root path for the job's own `programme_id`). This can be upgraded to fully separate storage per-programme later if a specific programme needs stronger blast-radius containment, without affecting the rest of the design.

### Incremental rollout on top of the base stack

1. Ship the base stack first (containerize → externalize state → split job runner → shared bus → reverse-proxy auth) — a prerequisite either way.
2. Add the `programmes` and `programme_members` tables; migrate the existing single root into the registry as programme #1.
3. Add the authorization check in the request hook; every route gets a `:programmeId` param and the ACL check.
4. Parameterize `App` construction so the server holds a small in-memory map of `programme_id -> App`-equivalent (queue partition, watcher, status cache), lazily created per programme, instead of one global instance.
5. Add the programme picker to the SPA and a minimal membership-management screen (an `owner` can add/remove a person for their programme).

Creating a brand-new programme (provisioning its storage subdirectory + inserting its `programmes` registry row) is treated as an out-of-band ops task for now — a short admin script/CLI run outside the app — rather than an in-app "create programme" flow, since this is expected to happen rarely at the current scale. Upgradeable to a UI-driven flow later if it becomes frequent.

## Extension: importing the existing in-progress programme

There is already a real programme underway locally: `working_area/` (a programme root with `programme.toml`, one module `KV7016`, ~22MB of content), `bcn sync`-linked to a Northumbria University OneDrive/SharePoint folder (`working_area/sync-state.json` records the remote and per-file sync state). This needs to become the deployment's first live programme, not a fresh empty one.

### What migrating this programme involves

- **Content**: `working_area/` itself (UI state is already kept outside the programme root by design — `app.ts:19-24`, so nothing to strip there) is the exact directory that needs to land at `<shared-volume>/kv7016/`. No content transformation needed — `bcn` reads the same layout regardless of where the disk lives.
- **Sync state**: `sync-state.json` (the per-file hash/conflict-tracking state used by the *internal* team-to-OneDrive `bcn sync` mechanism) is dropped — the reason for that mechanism, reconciling one editor's local disk against a shared OneDrive folder, goes away once everyone edits the same cloud copy directly. Run `bcn sync --pull` one last time locally first to make sure nothing unpulled is left behind. This is distinct from the ongoing need to exchange markdown with *external partners* (below), which is not being retired.
- **Programme registration**: this becomes the first row created by the "register a new programme" ops step above (registry row + `programme_members` entries for the team) — so this import naturally validates that step.
- **Job history**: today's job history lives in a local SQLite file outside the programme root, keyed to the `--root` path via a hash (`app.ts:20-24`) — disposable by original design. The plan is to start the deployment's job history empty for this programme rather than migrating SQLite rows into Postgres, matching how the app already treats a changed `--root` today (fresh data dir, empty history).

### Import procedure

1. **Before infra exists**: run `bcn sync --pull` locally against `working_area/`, then `bcn validate`/`bcn status` to capture a known-good baseline to diff against after the move.
2. **Once the shared volume exists**: copy the contents of `working_area/` (excluding `sync-state.json`) into it at `<volume>/kv7016/` — a one-time bulk copy, not a live sync.
3. **Register the programme**: run the ops script to insert the `programmes` row and add the relevant people to `programme_members`.
4. **Verify**: run `bcn status`/`bcn validate` against the new path (via the deployed worker, triggered from the UI) and diff against the pre-migration baseline — confirms the copy is complete and `bcn` recognizes it identically.
5. **Cut over**: once verified, treat `working_area/` on the local Mac as read-only/archived so nobody keeps editing the local copy after the deployment becomes the source of truth.

## Extension: exchanging markdown content with external partners

Retiring the internal `bcn sync` (team-to-OneDrive) mechanism does not retire a separate, ongoing requirement: markdown content still needs to go out to external partners for review, and their edits/new content need to come back in — a live collaboration workflow, not a backup concern.

`tooling/bcn/commands/translation.py` already implements almost exactly this shape of workflow (built for translation specifically, but confirmed to be a good fit for general partner exchange as-is):
- **`bcn translation --export`**: for a batch of topics, strips narration from the English markdown, bundles it with a `manifest.json` describing the batch and expected return format, into a timestamped zip.
- **`bcn translation --import <zip-or-dir>`**: extracts returned files by naming convention, writes them into place, re-validates everything in the batch, and returns one consolidated list of problems.

The gap for a networked deployment is narrow: today both directions assume a local filesystem path — there is no upload endpoint, no download link, no cloud storage integration. Two additions, not a redesign:
1. **Export delivery**: after `bcn translation --export` produces its zip (now on the shared volume), the API generates a short-lived signed download link that can be shared with the external partner directly.
2. **Import intake**: an upload endpoint on the API (new — today the browser can only reference a path the server already knows about, never push arbitrary bytes, per the explicit comment on the `import` arg in `jobs.ts:36`) that accepts a partner's returned zip, stores it on the shared volume, then submits the existing `translation --import` job pointing at it. `bcn`'s validation/consolidated-error-reporting logic needs no changes.

This is a small, well-scoped infra addition (signed-URL export delivery + an upload endpoint for import) around existing, already-correct `bcn` logic — and it's provider-agnostic (works the same whether the signed URL comes from a local object-store shim, Azure Blob SAS, or DigitalOcean Spaces, later).

## Extension: programme/theme configuration as stored, editable variables

Today every pipeline setting — validation thresholds, delivery bitrates, compose layout, QA duration bounds, bumper timings, theme choice — lives exclusively in hand-edited TOML files (`programme.toml`, `module.toml`, `themes/<name>/theme.toml`), deep-merged over a `DEFAULTS` dict in `config.py:22-141`. There is no existing pattern to extend for this: `SettingsPage.vue` only edits its own UI-only prefs (`db.ts` `prefs` table), explicitly scoped by its own header comment to exclude pipeline configuration; there is no `bcn config` command, and `bcn schema` only covers command *output* envelopes, not config. This is genuinely new surface, modeled closely on the existing `prefs` pattern (DB-backed values + defaults + merge + REST GET/PUT + a Vue form).

### Design: settings stay data-driven in the DB, materialized to TOML before each `bcn` invocation

`bcn` itself does not change how it reads config — it stays a tool that reads TOML off disk, keeping `config.py`'s existing merge/validation logic untouched. What changes is *who writes those files*: the API becomes the sole writer, generating TOML from values held in Postgres.

1. **Add a small `bcn config` counterpart to `schema.py`**: a new read-only command that dumps `config.py`'s `DEFAULTS` dict as a JSON Schema-like structure (key, type, default, doc string from the existing comments) — the machine-readable contract the UI/API build a settings form from, exactly as `bcn schema <tool>` already lets the UI generate types from command envelopes (`ui/shared/scripts/gen-envelopes.mjs`). Same pattern, applied to config.
2. **Store overrides, not full files, per programme**: a `programme_settings` table (`programme_id`, `key` e.g. `validate.forbidden`, `value` as JSON) holds only values actually overridden — mirroring how `programme.toml` today only needs overrides, since `config.py`'s `_merge()` already deep-merges over `DEFAULTS`. A parallel `module_settings` table covers the narrower `module.toml` override set (`theme`, `bumpers` — `config.py:143`).
3. **Materialize on write, not on read**: whenever a setting changes through the UI, the API regenerates that programme's `programme.toml` (and any affected `module.toml`) onto the shared volume from current DB values merged over defaults — a small, pure serialization step. `bcn` reads the file exactly as it does today; the CLI contract stays unchanged.
4. **Theme files stay files, not DB rows**: `theme.toml` plus its CSS/fonts/logos (`config.py:205-246`) is a bundle of real assets, better left as files on the shared volume (or shipped in the container image for the bundled `default` theme). Only *which* theme a programme/module selects (a string) goes through the settings system.
5. **UI**: extend `SettingsPage.vue`'s pattern (or add a sibling "Programme settings" page) with a form generated from the new `bcn config` schema — grouped by section the same way `DEFAULTS` is already grouped, each field showing its current value, its default, and a reset-to-default control.

### Why this shape and not the alternatives

- **Not env vars**: `config.py` deep-merges nested TOML tables (e.g. `[validate.severity]` is free-form); env vars represent nested/typed structure poorly.
- **Not passing config as job arguments**: `jobs.ts`'s `VALUE_ARGS`/`FLAG_ARGS` allowlist (`jobs.ts:28-43`) is deliberately narrow and per-invocation; piping the entire config dict through CLI args on every job would balloon that surface and duplicate `config.py`'s merge logic in TypeScript.
- **Materializing TOML is the smallest change**: keeps 100% of `config.py`'s existing validation/merge/error-reporting doing exactly what it does today; the only new code is "read DB rows, merge over defaults, write TOML" — a pure, testable function — plus the new `bcn config` schema command.

### Decided: no proactive staleness flagging on settings change

Changing a setting that affects already-built artifacts (e.g. `delivery.video_bitrate` after topics have been composed) does not trigger new invalidation logic — the next `bcn validate`/`status` run surfaces the mismatch the same way `bcn` already detects staleness via mtimes today.

### Rollout position

Sits on top of the multi-programme extension (settings are naturally per-`programme_id`); independent of the partner-exchange and existing-programme-import extensions. Suggested order: `bcn config` schema command first (pure Python, no infra dependency, testable standalone), then the settings tables and materialization function, then the UI form.

## Local development

Nearly everything above is developed and tested with `docker compose up` alone — no cloud account needed for the stack, the multi-programme extension, the import extension, the partner-exchange extension, or the settings extension, since all of it is plain containers, Postgres, Redis, and a volume. The only pieces that need a real provider account to verify end-to-end, once a provider is chosen, are provider-specific managed-service swaps from the "where this maps later" table (e.g. Azure Easy Auth/Entra ID specifically, or a real Azure Files/Spaces mount) — and those are deliberately deferred, not part of the base build.

## Open question

- Is a full multi-user/permissions model needed *within* a single programme (e.g. can every member do everything, or are finer-grained roles needed there too)? The multi-programme extension adds cross-programme ACLs but keeps within-programme permissions flat (everyone with `member`+ can run all jobs), matching today's single-operator behavior.

## Verification (once implementation begins)

- Local: `docker compose up` with api + worker + postgres + redis + a bind-mounted `example/` programme root; run a `render` job end-to-end and confirm SSE progress and final envelope match today's local-dev behavior (compare against `ui/server`'s existing vitest contract tests against `example/` and `fake-bcn.mjs`).
- Once deployed to a real host (droplet, VM, or managed compute): deploy with a copy of `example/`, run each job type (`validate`, `render`, `compose`, `package`, `qa`, `translation`, `sync`) through the real queue/worker path, confirm job history survives an API restart (proves externalized state actually decoupled from the process).
- Load: submit several overlapping-path jobs and confirm the advisory-lock/overlap logic still serializes them correctly under the new queue (previously guaranteed by a single in-memory `Map`; the one piece of correctness logic that must be re-verified after the move).
