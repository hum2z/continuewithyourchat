# previous

**Persistent memory for Claude Code chats. Type `/previous` and it's caught up.**

Every new session starts blank, so you end up re-explaining the same project,
the same constraints, and the same "no, we don't do it that way" corrections you
already gave last week. This removes that tax — and it costs nothing to run.

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/hum2z/continuewithyourchat/main/install.sh | bash
```

Then **start a new Claude Code session** — hooks are loaded at startup, so the
session you install from won't have them yet.

That's the whole install. You don't need this repo checked out to use the skill.
Re-run the same command any time to upgrade: it replaces the skill and leaves
your memory in `~/.claude/previous` untouched.

**Requirements:** Python 3.9+, `curl`, `tar`. The installer checks for all three
before writing anything.

<details>
<summary>Rather not pipe a script into bash?</summary>

Read it first:

```bash
curl -fsSL https://raw.githubusercontent.com/hum2z/continuewithyourchat/main/install.sh | less
```

Or install by hand:

```bash
git clone https://github.com/hum2z/continuewithyourchat.git
mkdir -p ~/.claude/skills
cp -r continuewithyourchat/.claude/skills/previous ~/.claude/skills/
python3 ~/.claude/skills/previous/scripts/pmem.py install-hook
```

The `mkdir` matters: without an existing `skills/` directory, `cp -r` copies the
skill's *contents* there instead of the folder itself, and Claude Code won't
find it.

</details>

### Uninstall

```bash
rm -rf ~/.claude/skills/previous     # remove the skill
rm -rf ~/.claude/previous            # remove your memory too, if you want
```

Then delete the two `pmem.py` entries from `hooks` in `~/.claude/settings.json`.
To switch it off without uninstalling, set `PREVIOUS_AUTO=0`.

---

## Using it

1. Run `/previous` in a project. The first time it'll say there's nothing yet —
   that's expected.
2. Work normally, then close the session. It captures itself silently.
3. Your next session prints one line:
   `[previous] Memory for you/repo — 2 open threads, 1 new session captured.`
4. Type `/previous` and it's caught up.

Give it two or three sessions before judging it — the digest has nothing to
compound from until then.

| Command | |
|---|---|
| `/previous` | catch up on this project |
| `/previous save` | distil and consolidate now |
| `/previous list` | every project with memory |
| `/previous forget <thing>` | remove something (backed up first) |

---

## How it works

Saving is automatic and free. A `SessionEnd` hook runs a plain Python script
over the transcript — no model involved, so it costs **zero tokens**.

It works because a transcript is almost entirely tool output. In a real session,
147k tokens of transcript contained just three things the user actually typed —
and those turns are where the decisions, corrections and preferences live.
Keeping them and discarding the rest loses almost no signal:

```
transcript   586 KB   ~147,000 tokens
capture      1.1 KB       ~280 tokens
```

Captures sit in `pending/` until your next `/previous`, when they're distilled
into a few durable bullets each. The expensive step — rewriting the digest —
happens once every ~5 sessions.

A `SessionStart` hook prints **one line** (~25 tokens) when memory exists,
quoting what a full load would cost so you can decide before paying it. It
could inject the whole digest, but that would charge every session for memory
including ones unrelated to the remembered work. So it points, and loading
stays yours.

### What it costs

| | |
|---|---|
| Capturing a session | **0 tokens** — pure Python at session end |
| Session-start pointer | ~25 tokens, one line |
| `/previous` on a healthy project | a few hundred tokens |
| Full digest rewrite | every ~5 sessions |

`pmem.py stats` reports `restore_cost_tokens`, so the number is never a
mystery. The digest is stripped of template boilerplate and empty sections
before loading, because that's a tax you'd otherwise pay every single session.

### How it compounds

The naive version is a log that grows forever, and it stops being useful at
exactly the point it becomes valuable — forty sessions in, nothing is findable
and loading it costs more than it saves.

So instead of appending, every consolidation **rewrites the digest as a whole**.
Resolved questions leave the open list. Superseded decisions collapse into one
line carrying the change. Facts that keep recurring get promoted. It gets
sharper over time rather than longer.

Two layers, so a new chat about project A doesn't get flooded with project B:

- **global** — true everywhere: how you like commits written, what to never touch
- **per-project** — keyed to the git remote, so the same repo on your laptop and
  in a container shares one memory

### What gets kept

The bar is: *would someone picking this up next week need it, and could they not
just read the code?*

Decisions **and their reasoning**. Corrections you made — the highest-value
lines in the file, because they cost you real effort to transmit. Preferences.
Environment gotchas. What's half-done. And dead ends: what was tried, what
broke, and why, which is cheap to record and expensive to rediscover.

Not kept: anything readable from the code, blow-by-blow narration, or debugging
that led nowhere.

---

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

Outside the repo and outside version control, because it's yours and it
accumulates things you wouldn't want to push. To sync across machines:

```bash
export PREVIOUS_HOME="$HOME/Dropbox/claude-previous"
```

## Staying in control

Capture is automatic but deliberately *inert* — the hook only ever writes raw
material into `pending/`. Nothing enters your real memory until a session
distils it, and nothing overwrites the digest without a backup first. So the
automation can't quietly corrupt anything; the worst it does is leave a file you
didn't want, which `clear-pending` removes.

`MEMORY.md` is plain markdown. Open it and edit it whenever you like — it's
meant to be read by humans too.

The plumbing is a small script if you want it directly:

```bash
python3 ~/.claude/skills/previous/scripts/pmem.py show    # what a restore loads
python3 ~/.claude/skills/previous/scripts/pmem.py stats   # sizes and restore cost
python3 ~/.claude/skills/previous/scripts/pmem.py list    # all remembered projects
```

## License

MIT — see [LICENSE](LICENSE).
