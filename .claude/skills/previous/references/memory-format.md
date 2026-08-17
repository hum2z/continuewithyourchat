# MEMORY.md — structure and a worked merge

## Section order, and why it's this order

`show` dumps the whole file into context, and attention is front-loaded, so the
sections run from "needed to re-enter the problem right now" to "needed
occasionally".

```markdown
# Memory — owner/repo

## Snapshot
## Stable facts
## Preferences & conventions
## Decisions
## Open threads
## Dead ends
```

**Snapshot** — 3-8 lines. Where things stand *today*. Rewritten from scratch
every save; nothing here survives untouched. This is the section that answers
"what were we doing", so it earns the top spot.

**Stable facts** — the things that stop being worth re-deriving. Stack,
architecture in a few lines, key file paths, the commands that actually work
(`pnpm test:unit`, not `npm test`), deploy target, service dependencies. Facts
graduate into this section once they've proven they aren't going to change.

**Preferences & conventions** — how this person wants work done *here*.
Anything that would apply in any project belongs in the global layer instead.

**Decisions** — dated one-liners, each carrying its reason. The reason is not
optional; a decision without one gets re-litigated by the next session that
doesn't see why it was made.

**Open threads** — live questions and half-done work. The section that should
shrink as often as it grows. Anything resolved leaves.

**Dead ends** — tried, failed, why. The cheapest section to write and the one
that saves the most time, because failed approaches are exactly what a fresh
session is most likely to try again.

Sections that are empty can be left out. A short honest file beats a complete
skeleton of headings with nothing under them.

## Style

- Short declarative lines. Bullets, not paragraphs.
- Date anything time-sensitive: `(2026-03)`.
- Name real things: `src/auth/refresh.ts:88`, `RefreshLock`, `STAGING_CERT`.
- Write for a stranger. "The usual problem" means nothing in four months.
- No hedging, no narration, no self-assessment.

---

## A worked merge

The point of this example is that the file after the merge is **not longer**
than the file before, despite covering an additional session. Two entries
collapsed, one thread closed, one fact got promoted.

### Before

```markdown
## Snapshot

Building token refresh for the auth service. Refresh currently happens inline
in the request path, which is the thing we're trying to change.

## Stable facts

- Node 20, TypeScript, Fastify. Postgres via Prisma.
- Tests: `pnpm test:unit`. `npm test` exists but runs the wrong config.

## Decisions

- 2026-03-02: Refresh moves onto the worker queue rather than staying inline —
  inline refresh was blocking request threads under load.

## Open threads

- Should refresh tokens have a TTL in Redis, or rely on the DB record only?
- Staging deploy blocked — cert expired, waiting on infra.
```

### The session that follows

They implement the queue-based refresh, hit a race where two workers grab the
same token, fix it with a Redis lock, and in the process settle the TTL
question (Redis TTL, matched to the DB expiry). They also mention in passing,
for the second time, that `npm test` runs the wrong config. Staging is still
blocked. They try a Postgres advisory lock first and abandon it.

### After

```markdown
## Snapshot

Queue-based token refresh is implemented and working. Concurrency is handled by
a Redis lock in `src/auth/RefreshLock.ts`. Staging deploy still blocked on the
expired cert — nothing to do until infra responds.

## Stable facts

- Node 20, TypeScript, Fastify. Postgres via Prisma. Redis for locks + token TTL.
- Tests: `pnpm test:unit`. Do not use `npm test` — it picks up the wrong config
  and fails in a way that looks like a real failure.

## Decisions

- 2026-03-02: Refresh runs on the worker queue, not inline — inline was blocking
  request threads under load.
- 2026-03-09: Refresh tokens carry a Redis TTL matched to the DB expiry. Closes
  the open question from 03-02; two sources of truth were drifting.
- 2026-03-09: Concurrency via Redis lock (`RefreshLock`), not Postgres advisory
  locks.

## Open threads

- Staging deploy blocked — cert expired, waiting on infra (since 03-02).

## Dead ends

- Postgres advisory locks for refresh concurrency (2026-03-09) — the lock is
  tied to the session, and the worker pool recycles connections, so locks were
  released early under load.
```

### What happened, and why

- **The TTL thread didn't get an answer appended — it left.** It moved to
  *Decisions* with its reason. The open-threads list is now honest: one item,
  genuinely open.
- **The `npm test` gotcha got promoted and sharpened.** It came up twice, so
  it's structural, and the note now says what the failure *looks like* — which
  is the part that costs an hour when you don't know it.
- **The abandoned approach became a Dead end with its mechanism.** "Advisory
  locks didn't work" would be nearly useless. The connection-recycling detail
  is what stops someone trying it again.
- **The snapshot was rewritten, not amended.** It describes now, not the
  journey to now. The journey is what `log.md` is for.
- **The stale blocker kept its date.** `(since 03-02)` is the signal that it
  may need chasing rather than waiting on.
