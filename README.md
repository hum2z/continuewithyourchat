# previous

A Claude Code skill that gives your chats a memory.

Every new session starts blank, so you end up re-explaining the same project,
the same constraints, and the same "no, we don't do it that way" corrections
you already gave last week. This removes that tax.

```
/previous          → catch up: loads what we know about this project
/previous save     → distil this session into memory
```

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
```

Then start a new session and run `/previous`. On a fresh project it'll tell you
there's nothing yet — that's the expected first run.

Requires Python 3.9+, which you almost certainly already have.

## Where memory lives

```
~/.claude/previous/
├── global/MEMORY.md
└── projects/<repo>/
    ├── MEMORY.md      the living digest
    ├── log.md         one entry per session
    ├── backups/       last 5 versions, taken before every rewrite
    └── archive/       folded-away old log entries
```

Outside the repo and outside version control, because it's yours and it tends
to accumulate things you wouldn't want to push. To sync it across machines,
point `PREVIOUS_HOME` at a synced folder:

```bash
export PREVIOUS_HOME="$HOME/Dropbox/claude-previous"
```

## Saving

Saves are manual — nothing is written unless you ask. Claude will offer at
natural stopping points (something just landed, the session is winding down,
you said something clearly durable), but it won't act on its own. A memory
file that fills up with unrequested checkpoints stops being trustworthy, and
trust is the whole point.

`MEMORY.md` is plain markdown. Open it and edit it whenever you like — it's
meant to be read by humans too.

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
