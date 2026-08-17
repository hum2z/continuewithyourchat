# previous

A Claude Code skill that gives your chats a memory.

Every new session starts blank, so you end up re-explaining the same project,
the same constraints, and the same "no, we don't do it that way" corrections
you already gave last week. This removes that tax.

```
/previous          → catch up: loads what we know about this project
```

Sessions save themselves. You only ever type `/previous`.

## Saving is automatic, and free

A `SessionEnd` hook fires when a session closes and runs a plain Python script
over the transcript. No model involved, so it costs **zero tokens**.

It works because a transcript is almost entirely tool output. In a real
session, 147k tokens of transcript contained just three things the user
actually typed — and those turns are where the decisions, corrections, and
preferences live. Keeping them and discarding the rest is a 500× reduction with
essentially no loss of signal:

```
transcript   586 KB   ~147,000 tokens
capture      1.1 KB       ~280 tokens
```

Those captures sit in `pending/` until the next time you run `/previous`, when
they get distilled into a few durable bullets each. The expensive step — a full
rewrite of the digest — only happens once every ~5 sessions.

Turn it on with:

```bash
python3 ~/.claude/skills/previous/scripts/pmem.py install-hook
```

It merges into your existing `~/.claude/settings.json` (backing it up first,
and refusing to touch it if it isn't valid JSON). Set `PREVIOUS_AUTO=0` to
pause it without uninstalling.

## And a nudge when you start

The same command installs a `SessionStart` hook that prints **one line** when
memory exists for the project you just opened:

```
[previous] Memory for hum2z/continuewithyourchat — 1 open thread, 2 new
sessions captured. Run /previous to load it (~185 tokens).
```

~25 tokens, and it quotes the load cost so you can decide before paying it.
It could inject the whole digest — `SessionStart` output does reach the model —
but that would charge every session for memory, including ones that have
nothing to do with the remembered work. So it points, and loading stays yours.
Nothing is printed at all when a project has no memory.

## What it costs you

| | |
|---|---|
| Capturing a session | **0 tokens** — pure Python at session end |
| `/previous` on a healthy project | a few hundred tokens |
| Distilling a captured session | ~300 in, ~100 out, once |
| Full digest rewrite | every ~5 sessions |

`pmem.py stats` reports `restore_cost_tokens` so the number is never a mystery.
The digest is also stripped of template boilerplate and empty sections before
it's loaded, because that's a tax you'd otherwise pay on every single session.

## How it compounds

The naive version of this is a log that grows forever, and it stops being
useful at exactly the point it becomes valuable — forty sessions in, nothing
is findable and loading it costs more context than it saves.

So instead of appending, every save **rewrites the digest as a whole**,
reconciling what's new against what's already there. Resolved questions leave
the open list. Superseded decisions get replaced with one line carrying the
change. Facts that keep recurring get promoted. The file gets sharper over
time rather than longer.

Two layers, so a new chat about project A doesn't get flooded with project B:

- **global** — things true everywhere: how you like commits written, what you
  never want touched
- **per-project** — keyed to the git remote, so the same repo on your laptop
  and in a container shares one memory

## What gets kept

The bar is: *would someone picking this up next week need it, and could they
not just read the code?*

Decisions **and their reasoning**. Corrections you made — those are the highest
value thing in the file, because they cost you effort to transmit. Preferences.
Environment gotchas. What's half-done. And dead ends: what was tried, what
broke, and why, which is cheap to write down and expensive to rediscover.

Not kept: anything readable from the code, blow-by-blow narration, or
debugging that led nowhere.

## Install

Copy the skill into your Claude Code skills directory:

```bash
git clone https://github.com/hum2z/continuewithyourchat.git
cp -r continuewithyourchat/.claude/skills/previous ~/.claude/skills/
python3 ~/.claude/skills/previous/scripts/pmem.py install-hook
```

Then start a new session and run `/previous`. On a fresh project it'll tell you
there's nothing yet — that's the expected first run. Work normally, close the
session, and the next `/previous` will know what you did.

Requires Python 3.9+, which you almost certainly already have.

## Where memory lives

```
~/.claude/previous/
├── global/MEMORY.md
└── projects/<repo>/
    ├── MEMORY.md      the living digest
    ├── log.md         one distilled entry per session
    ├── pending/       raw auto-captures, cleared once distilled
    ├── backups/       last 5 versions, taken before every rewrite
    └── archive/       folded-away old log entries
```

Outside the repo and outside version control, because it's yours and it tends
to accumulate things you wouldn't want to push. To sync it across machines,
point `PREVIOUS_HOME` at a synced folder:

```bash
export PREVIOUS_HOME="$HOME/Dropbox/claude-previous"
```

## Staying in control

Capture is automatic, but it's deliberately *inert* — the hook only ever writes
raw material into `pending/`. Nothing enters your actual memory until a session
distils it, and nothing overwrites the digest without a backup being taken
first. So the automation can't quietly corrupt anything; the worst it does is
leave a file you didn't want, which `clear-pending` removes.

`MEMORY.md` is plain markdown. Open it and edit it whenever you like — it's
meant to be read by humans too. You can also still drive it by hand:

```
/previous save           distil and consolidate right now
```

If you'd rather it never ran on its own, skip `install-hook` (or set
`PREVIOUS_AUTO=0`) and the manual flow works exactly as before.

## Other commands

```
/previous list           every project with memory, and when it was last touched
/previous edit           open this project's MEMORY.md directly
/previous forget <thing> remove something (backed up first)
```

The underlying plumbing is a small script if you want it directly:

```bash
python3 ~/.claude/skills/previous/scripts/pmem.py show
python3 ~/.claude/skills/previous/scripts/pmem.py stats
```
