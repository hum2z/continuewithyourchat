---
name: previous
description: Persistent memory across chat sessions. Restores what happened in earlier conversations about this project, and saves the current one so the next chat starts informed instead of blank. Use this whenever the user runs /previous, or says anything like "catch me up", "what were we doing", "continue where we left off", "pick up from last time", "you should remember this", "save this for next time", "checkpoint this", or "remind me what we decided". Also use it at the START of a session when the user refers to earlier work as if you should already know it ("the thing we built yesterday", "that bug we were chasing") — that means memory exists and should be loaded before answering. And offer it at the END of a substantial session, before the context is lost.
---

# previous

Chat sessions are amnesiac. Every new one starts from nothing, so the user
re-explains the same project, the same constraints, the same "no, we don't do
it that way" corrections they gave last week. That tax is what this skill
removes.

It maintains a small, hand-tended digest per project that gets **rewritten and
re-distilled** on every save. The key word is rewritten. A log that only ever
grows becomes useless at exactly the moment it becomes valuable — 40 sessions
in, nobody can find anything, and loading it costs more context than it saves.
Compounding means the digest gets *smarter* over time, not longer.

## Where things live

```
~/.claude/previous/
├── global/                  things true everywhere: who they are, how they like things
│   ├── MEMORY.md
│   └── log.md
└── projects/<slug>/         one per repo, keyed by git remote (or path hash)
    ├── MEMORY.md            the living digest — rewritten in full on each save
    ├── log.md               append-only, one entry per session
    ├── meta.json
    ├── backups/             last 5 versions of MEMORY.md
    └── archive/             folded-away old log entries
```

Use `scripts/pmem.py` for all path resolution, reading, appending, backups, and
rotation. It exists so you never have to guess where a file is or hand-roll the
scoping logic. Run it from the project directory:

```bash
python3 <skill>/scripts/pmem.py show          # global + project memory + recent sessions
python3 <skill>/scripts/pmem.py stats         # sizes, and whether it's time to re-compact
python3 <skill>/scripts/pmem.py path          # where everything lives, as JSON
python3 <skill>/scripts/pmem.py list          # every project with memory
python3 <skill>/scripts/pmem.py backup        # snapshot MEMORY.md before rewriting it
python3 <skill>/scripts/pmem.py log --title T # append a session entry (body on stdin)
python3 <skill>/scripts/pmem.py archive       # rotate log.md after folding it in
```

Every command takes `--global` to target the cross-project layer instead
(except `show` and `stats`, which always report both).

## Dispatch

| What the user says | What to do |
|---|---|
| `/previous`, "catch me up", "where were we" | **Restore** |
| `/previous save`, "save this", "checkpoint" | **Save** |
| `/previous list` | Run `pmem.py list`, summarise briefly |
| `/previous edit` | Open the project `MEMORY.md` for direct editing |
| `/previous forget <thing>` | Find it in `MEMORY.md`, back up, remove it, confirm what went |

---

## Restore

1. Run `pmem.py show`. If it reports no memory, say so in one line and carry
   on with the actual request — do not treat a fresh start as a problem.
2. Read what comes back. It is now your working context; do not print it back
   to the user.
3. Give a **short orientation**, not a recital. Aim for something like:

   > Picked up where we left off on **auth-service**. Last session you moved
   > token refresh onto the worker queue and hit a race in `RefreshLock`.
   > Open: the Redis TTL question, and the staging deploy is still blocked on
   > the cert. You'd decided against the middleware approach.

   Three to six lines. Lead with the most recent state, then open threads,
   then anything they'd want to be reminded they'd already ruled out.
4. If they asked a question alongside `/previous`, answer it — the restore is
   the setup, not the deliverable.

The reason to keep the orientation tight: they wrote this memory so they could
*skip* the recap, and a wall of text is the recap. What they need is enough to
re-enter the problem, plus the confidence that you know the rest.

## Save

**Back up before you write.** Run `pmem.py backup` first, always. You are about
to replace a file that represents weeks of accumulated context, and the failure
mode — a bad rewrite that quietly drops half of it — is invisible until much
later.

Then:

1. Run `pmem.py stats` to see whether you're over budget and need to be
   aggressive about pruning.
2. Read the current project `MEMORY.md` in full. You cannot merge into
   something you haven't read.
3. Go back over **this** session and pull out what's durable (see below).
4. Rewrite `MEMORY.md` as a whole — the old content and the new, reconciled
   into one document. Not the old file with a new section stapled on.
5. Append a short session entry to `log.md` via `pmem.py log` — 3-6 bullets on
   what actually happened, so there's a raw record behind the digest.
6. If anything belongs to the *user* rather than the project — a preference, a
   working style, a standing instruction — put it in the global layer with
   `--global` instead.
7. Tell them in one line what you saved and roughly how big the digest is now.

### What's worth keeping

The test is: **would a competent stranger picking this up next week need it,
and could they not get it from reading the code?**

Worth keeping:

- **Decisions and the reasoning behind them.** The reasoning matters more than
  the decision — without it, the next session re-opens a settled question.
- **Corrections the user made.** When someone says "no, we use X here", that's
  a durable fact about their world that cost them effort to transmit. These
  are the single highest-value thing in the file.
- **Preferences and conventions.** How they want commits written, what they
  never want touched, whether they want to be asked before big refactors.
- **Dead ends.** What was tried, what broke, why it was abandoned. Cheap to
  record, expensive to rediscover, and almost always forgotten.
- **Environment gotchas.** The test that only passes with a flag, the service
  that must be running, the thing that looks broken but isn't.
- **In-flight state.** What's half-done and what the next move was going to be.

Not worth keeping:

- Anything readable from the code in ten seconds. The memory should point at
  the codebase, not duplicate it.
- Blow-by-blow narration of the session.
- Transient debugging that led nowhere and taught nothing.
- Praise, filler, and your own commentary on how the session went.

### How to merge

This is the part that determines whether the file is still useful a year in.

- **Supersede, don't accumulate.** If a new decision overrides an old one,
  replace it and note the change — `Moved to Postgres (was SQLite; hit
  concurrent-write limits, 2026-03)`. One line carrying both the current state
  and the history beats two lines contradicting each other.
- **Resolve open threads.** Anything in *Open threads* that got settled this
  session moves into *Decisions* or disappears. A stale open thread is worse
  than no note, because it sends the next session chasing something finished.
- **Merge duplicates.** Three sessions each noting the same env quirk collapse
  to one clear line.
- **Promote repeats.** Something that keeps recurring across sessions has
  proven it's structural — move it up into *Stable facts* where it'll be read
  first.
- **Let things die.** A decision nobody has touched in months, about code that
  no longer exists, is noise. Dropping it is part of the job. (The backups are
  right there if you're wrong.)
- **Keep the voice plain.** Short declarative lines. This is a working note,
  not a report.

### Budgets

`pmem.py stats` flags these; treat them as pressure to distil, not hard limits:

- `MEMORY.md` over ~400 lines → merge harder, drop resolved items.
- `log.md` over ~600 lines or ~40 entries → fold anything still durable into
  `MEMORY.md`, then run `pmem.py archive` to rotate it.

If a save would push the digest well past budget and there's genuinely nothing
to cut, say so rather than silently letting it bloat — that usually means the
project has grown enough to want splitting, and the user should get to decide.

The structure of `MEMORY.md` and a worked before/after example of a good merge
are in `references/memory-format.md`. Read it the first few times you do a
save, especially if the existing file is large or messy.

---

## Offering to save

The user chose manual saves with nudges, so the nudge is your job — they'll
usually be deep in the work and won't think of it.

Offer once, in a single line, when:

- A substantial piece of work just landed (feature done, bug fixed, decision
  reached) and the session is winding down.
- The conversation has gone long and is clearly rich in context worth keeping.
- They say something that's plainly durable — a preference, a constraint, a
  correction — mid-session. Then it's worth asking whether to save it now, so
  it survives even if the session ends abruptly.

Don't offer after trivial exchanges, don't offer twice in a row after being
declined, and never save without being asked. A memory file that fills up with
unrequested checkpoints stops being trustworthy, and trust is the whole
product here.
