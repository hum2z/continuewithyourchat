---
name: previous
description: Persistent memory across chat sessions. Restores what happened in earlier conversations about this project, and keeps that memory compacted as it grows. Use this whenever the user runs /previous, or says anything like "catch me up", "what were we doing", "continue where we left off", "pick up from last time", "you should remember this", "save this for next time", "checkpoint this", or "remind me what we decided". Also use it at the START of a session when the user refers to earlier work as if you should already know it ("the thing we built yesterday", "that bug we were chasing") — that means memory exists and should be loaded before answering.
---

# previous

Chat sessions are amnesiac. Every new one starts from nothing, so the user
re-explains the same project, the same constraints, the same "no, we don't do
it that way" corrections they gave last week. That tax is what this removes.

## The shape of it

Three tiers, cheapest first. This split exists because memory is a cost that
recurs *every session forever*, so the expensive step has to be rare:

| Tier | When | Model cost |
|---|---|---|
| **Capture** | Automatically, at session end (`SessionEnd` hook) | **Zero** — pure Python |
| **Distil** | Next `/previous`, if captures are waiting | ~300 in / ~100 out per session |
| **Consolidate** | Once ~5 log entries have built up | One rewrite, amortised |

Capture is free because it never involves you: a script reads the raw
transcript and keeps only what a human actually typed. In a real session that
turned 147k tokens of transcript into 280 tokens of capture. Everything else
was tool output — bulk without durable signal.

```
~/.claude/previous/
├── global/MEMORY.md         things true everywhere (preferences, working style)
└── projects/<slug>/         keyed by git remote, so laptop and container share
    ├── MEMORY.md            the digest — rewritten whole when consolidating
    ├── log.md               distilled session entries
    ├── pending/             raw auto-captures awaiting distillation
    ├── backups/  archive/
```

## Commands

Run `scripts/pmem.py` from the project directory — it handles scope resolution,
reading, backups and rotation so you never guess at paths:

```bash
python3 <skill>/scripts/pmem.py show           # global + project + pending (the restore payload)
python3 <skill>/scripts/pmem.py show --brief    # digest only, skips log and captures
python3 <skill>/scripts/pmem.py stats           # sizes, restore cost in tokens, what needs doing
python3 <skill>/scripts/pmem.py log --title T   # append a distilled entry (body on stdin)
python3 <skill>/scripts/pmem.py clear-pending   # drop captures once distilled
python3 <skill>/scripts/pmem.py backup          # snapshot MEMORY.md before rewriting
python3 <skill>/scripts/pmem.py archive         # rotate log.md
python3 <skill>/scripts/pmem.py list            # every project with memory
python3 <skill>/scripts/pmem.py install-hook    # turn on automatic capture
```

`--global` targets the cross-project layer. `PREVIOUS_AUTO=0` pauses capture.

| What the user says | What to do |
|---|---|
| `/previous`, "catch me up" | **Restore** |
| `/previous save`, "checkpoint" | **Save** |
| "turn on auto-save" | `install-hook`, then confirm in one line |
| `/previous list` | `pmem.py list`, summarise briefly |
| `/previous forget <thing>` | Back up, remove it from `MEMORY.md`, confirm what went |

---

## Restore

1. Run `pmem.py show`. If it reports no memory, say so in one line and get on
   with the actual request — a fresh start is not a problem to be solved.
2. **If it lists pending captures, distil them now** (below). This is the only
   moment that reliably happens, so it's where automatic memory gets folded in.
3. Give a **short orientation** — three to six lines, not a recital:

   > Picked up on **auth-service**. Last session you moved token refresh onto
   > the worker queue and hit a race in `RefreshLock`. Open: the staging cert.
   > You'd already ruled out the middleware approach.

   Lead with current state, then open threads, then anything they'd want to be
   reminded they'd ruled out. They wrote this memory to *skip* the recap; a
   wall of text is the recap.
4. If they asked a question alongside `/previous`, answer it. The restore is
   setup, not the deliverable.

### Distilling pending captures

A capture is raw: the user's turns verbatim, files touched, notable commands.
Your job is to compress each into **3-6 bullets** of what's durable, append it
with `pmem.py log --title "<short title>"`, then run `pmem.py clear-pending`.

Compress hard. A capture holds everything a session *mentioned*; a log entry
holds only what a future session would need. Most sessions yield two or three
bullets, and some yield none — if a captured session produced nothing durable,
say so and clear it rather than manufacturing filler.

Then check `pmem.py stats`: once there are ~5 log entries, consolidate.

## Save

Two different jobs share this name; pick by what's actually accumulated.

**Light save** — append a distilled entry to `log.md` and stop. This is right
when the session produced a few facts but the digest is still accurate. Cheap,
and most saves should be this.

**Consolidation** — rewrite `MEMORY.md` whole. Do this when ~5 entries have
built up, when `stats` says you're over budget, or when the digest has gone
stale enough to mislead.

To consolidate:

1. **`pmem.py backup` first, always.** You're about to replace a file holding
   weeks of context, and a bad rewrite that quietly drops half of it stays
   invisible until the moment it matters.
2. Read the current `MEMORY.md` in full — you can't merge into something you
   haven't read — plus the log entries since the last consolidation.
3. Rewrite it as one document: old and new reconciled. Not the old file with a
   new section stapled on.
4. `pmem.py archive` if the log has grown past budget.
5. Anything about the *user* rather than the project — a preference, a standing
   instruction — goes to the global layer with `--global`.
6. Report in one line what you saved and the new restore cost from `stats`.

### What's worth keeping

The test: **would a competent stranger picking this up next week need it, and
could they not get it from reading the code?**

Keep decisions **and their reasoning** — without the why, the next session
re-opens a settled question. Keep corrections the user made ("no, we use X
here"); those are the highest-value lines in the file because they cost real
effort to transmit. Keep preferences, environment gotchas, what's half-done,
and dead ends — what was tried, what broke, why — which are cheap to record and
expensive to rediscover.

Drop anything readable from the code in ten seconds, blow-by-blow narration,
transient debugging that taught nothing, and your own commentary on how the
session went. The memory should point at the codebase, not duplicate it.

### How to merge

This is what makes the file compound instead of accrete:

- **Supersede, don't accumulate.** `Moved to Postgres (was SQLite; hit
  concurrent-write limits, 2026-03)` — one line carrying current state *and*
  history beats two lines contradicting each other.
- **Resolve open threads.** Anything settled leaves the open list. A stale open
  thread is worse than no note; it sends the next session chasing finished work.
- **Merge duplicates.** Three sessions noting the same quirk collapse to one.
- **Promote repeats.** Something recurring across sessions has proven it's
  structural — move it into *Stable facts*, which gets read first.
- **Let things die.** A decision about code that no longer exists is noise.
  Dropping it is part of the job; the backups are right there if you're wrong.

Budgets from `stats`: `MEMORY.md` past ~400 lines means merge harder; `log.md`
past ~40 entries means fold and archive. If a save would blow the budget and
there's genuinely nothing to cut, say so — that usually means the project has
outgrown one file, and the user should get to decide.

`references/memory-format.md` has the section layout and a worked before/after
merge where the file covers an extra session and comes out *shorter*. Read it
the first few times you consolidate, or when the existing file is messy.

## Cost discipline

The user is paying for this on every session, so treat restore size as a number
you're responsible for. `stats` reports `restore_cost_tokens`; a healthy
project sits in the hundreds. If it drifts past ~2k, the digest has stopped
being a digest — consolidate rather than letting it slide.

Two habits that matter: never print restored memory back to the user (it's
already in your context — repeating it doubles the cost for zero gain), and
prefer `show --brief` when you only need orientation and not the full history.

## Offering to save

Capture is automatic once the hook is installed, so the user doesn't need
prompting to preserve a session — it's already preserved. Mention saving only
when consolidation is genuinely due (`stats` says so), or when they've said
something durable that they'd want in the *global* layer, which capture won't
classify on its own.

If the hook isn't installed, offer `install-hook` once — after that, drop it.
Nagging about memory hygiene is its own kind of tax.
