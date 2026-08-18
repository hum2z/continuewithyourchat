#!/usr/bin/env python3
"""pmem — plumbing for the `previous` skill.

Handles the boring, deterministic parts of persistent chat memory: figuring out
which project you're in, where its memory lives, reading it back as one blob,
appending log entries, taking backups before rewrites, and reporting when a
file has grown past the point where it should be re-compacted.

The actual compaction — deciding what's worth keeping and merging it into the
existing digest — is model work and deliberately lives in SKILL.md, not here.

Usage:
    pmem.py path   [--global] [--dir DIR]
    pmem.py show   [--log-entries N] [--dir DIR]
    pmem.py log    [--global] [--dir DIR] [--title T] [--file F]   (else stdin)
    pmem.py stats  [--dir DIR]
    pmem.py list
    pmem.py backup [--global] [--dir DIR]
    pmem.py archive [--global] [--dir DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("PREVIOUS_HOME", Path.home() / ".claude" / "previous"))
GLOBAL_DIR = ROOT / "global"
PROJECTS_DIR = ROOT / "projects"

# Soft budgets. Going over is not an error — it is a signal to re-compact.
MEMORY_LINE_BUDGET = 400
LOG_LINE_BUDGET = 600
LOG_ENTRY_BUDGET = 40
KEEP_BACKUPS = 5

# A session's durable content lives almost entirely in what the user typed --
# in a real session, 108 transcript records / 378KB boiled down to 4 user turns.
# Capping each turn keeps a captured session around 1k tokens instead of 90k.
MAX_TURN_CHARS = 700
MAX_TURNS = 25
MAX_FILES = 25
MAX_CMDS = 12
CONSOLIDATE_AFTER = 5  # log entries before MEMORY.md is worth rewriting

SYS_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
CMD_TAG_RE = re.compile(r"<command-(name|message|args|contents)>.*?</command-\1>", re.S)
INTERESTING_CMD_RE = re.compile(
    r"\b(git (commit|push|revert|merge|rebase|tag)|pytest|jest|vitest|"
    r"(npm|pnpm|yarn|make|cargo|go|mix) (test|run|build)|tox|rspec)\b"
)

MEMORY_TEMPLATE = """# Memory — {label}

_Started {date}. Rewritten in full on each save; do not append blindly._

## Snapshot

_Where things stand right now, in a few lines._

## Stable facts

_Things that stay true: stack, architecture, key paths, commands, env quirks._

## Preferences & conventions

_How this person likes things done here._

## Decisions

_Dated, with the reason. Mark superseded ones rather than silently deleting._

## Open threads

_Unresolved work and questions. Close them out when they resolve._

## Dead ends

_Tried, did not work, why. Stops the same wall getting walked into twice._
"""

GLOBAL_TEMPLATE = """# Memory — global

_Things true no matter which project is open. Keep this short and durable;
anything project-specific belongs in that project's memory instead._

## About me

## Preferences & conventions

## Recurring gotchas
"""

LOG_TEMPLATE = """# Session log — {label}

_Append-only. One entry per session, newest at the bottom. Entries get folded
into MEMORY.md and archived once this file grows past its budget._
"""


# --------------------------------------------------------------------------- #
# scope resolution
# --------------------------------------------------------------------------- #

def _run(cmd: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    value = out.stdout.strip()
    return value or None


def project_root(start: Path) -> Path:
    top = _run(["git", "rev-parse", "--show-toplevel"], start)
    return Path(top) if top else start


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", text).strip("-").lower()
    return slug[:60] or "unnamed"


def scope_for(start: Path) -> tuple[str, str, Path]:
    """Return (slug, human label, project root) for the directory `start`.

    Prefers the git remote so the same repo cloned to two different paths — a
    laptop and a remote container, say — shares one memory. Falls back to the
    directory name plus a short path hash, which keeps two unrelated folders
    that happen to share a basename from colliding.
    """
    root = project_root(start)

    remote = _run(["git", "remote", "get-url", "origin"], root)
    if remote:
        cleaned = re.sub(r"^.*?[:/]{1,3}", "", remote.replace("git@", ""))
        cleaned = re.sub(r"\.git$", "", cleaned)
        parts = [p for p in cleaned.split("/") if p][-2:]
        if parts:
            return _slugify("--".join(parts)), "/".join(parts), root

    digest = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:8]
    return f"{_slugify(root.name)}--{digest}", root.name, root


def repo_scope_dir(start: Path) -> Path | None:
    """Repo-local memory, if this project has opted into it.

    Ephemeral environments — Claude Code on the web, CI containers — throw away
    the home directory between sessions, so `~/.claude/previous` has nothing to
    persist into. The repo is the only thing that survives, so a project can
    keep its memory in `.claude/previous/` and carry it in version control.
    """
    candidate = project_root(start) / ".claude" / "previous"
    return candidate if candidate.is_dir() else None


def scope_dir(start: Path, use_global: bool) -> Path:
    # The global layer stays in $HOME even in repo mode: it is cross-project by
    # definition, so burying it inside one repo would be wrong. On an ephemeral
    # host that means global memory does not persist — a real limitation, but
    # preferable to one project silently owning everyone's preferences.
    if use_global:
        return GLOBAL_DIR
    repo = repo_scope_dir(start)
    if repo is not None:
        return repo
    slug, _, _ = scope_for(start)
    return PROJECTS_DIR / slug


def ensure_scope(start: Path, use_global: bool) -> Path:
    """Create the scope directory and seed its files if missing."""
    d = scope_dir(start, use_global)
    d.mkdir(parents=True, exist_ok=True)

    if use_global:
        label, root = "global", None
    else:
        _, label, root = scope_for(start)

    memory = d / "MEMORY.md"
    if not memory.exists():
        body = (
            GLOBAL_TEMPLATE
            if use_global
            else MEMORY_TEMPLATE.format(label=label, date=_today())
        )
        memory.write_text(body, encoding="utf-8")

    log = d / "log.md"
    if not log.exists():
        log.write_text(LOG_TEMPLATE.format(label=label), encoding="utf-8")

    meta_path = d / "meta.json"
    if not meta_path.exists():
        meta_path.write_text(
            json.dumps(
                {
                    "label": label,
                    "project_root": str(root) if root else None,
                    "created": _now(),
                    "updated": _now(),
                    "sessions": 0,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _strip_scaffolding(md: str) -> str:
    """Remove template hint lines and empty sections before handing memory to a
    model. Restore happens every session, so boilerplate that never changes is
    a tax paid over and over — the italic prompts under each heading are for
    whoever edits the file by hand, not for the reader."""
    kept = [
        ln
        for ln in md.splitlines()
        if not (ln.strip().startswith("_") and ln.strip().endswith("_") and len(ln.strip()) > 2)
    ]

    out: list[str] = []
    i = 0
    while i < len(kept):
        line = kept[i]
        if line.startswith("## "):
            j = i + 1
            body: list[str] = []
            while j < len(kept) and not kept[j].startswith(("## ", "# ")):
                body.append(kept[j])
                j += 1
            if any(b.strip() for b in body):
                out.append(line)
                out.extend(body)
            i = j
        else:
            out.append(line)
            i += 1

    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _tokens(text: str) -> int:
    """Rough token estimate — good enough to reason about budget."""
    return len(text) // 4


def _bump_meta(d: Path, **fields) -> None:
    path = d / "meta.json"
    try:
        meta = json.loads(_read(path) or "{}")
    except json.JSONDecodeError:
        meta = {}
    meta.update(fields)
    meta["updated"] = _now()
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_path(args) -> int:
    start = Path(args.dir).resolve()
    slug, label, root = scope_for(start)
    d = scope_dir(start, args.use_global)
    print(
        json.dumps(
            {
                "slug": "global" if args.use_global else slug,
                "label": "global" if args.use_global else label,
                "mode": "repo" if repo_scope_dir(start) else "home",
                "project_root": str(root),
                "scope_dir": str(d),
                "memory": str(d / "MEMORY.md"),
                "log": str(d / "log.md"),
                "exists": d.exists(),
                "global_dir": str(GLOBAL_DIR),
                "global_memory": str(GLOBAL_DIR / "MEMORY.md"),
            },
            indent=2,
        )
    )
    return 0


def _tail_entries(log_text: str, n: int) -> str:
    """Return the last n `## `-headed entries from a log file."""
    if n <= 0:
        return ""
    chunks = re.split(r"^(?=## )", log_text, flags=re.M)
    entries = [c for c in chunks if c.startswith("## ")]
    if not entries:
        return ""
    return "".join(entries[-n:]).rstrip()


def cmd_show(args) -> int:
    start = Path(args.dir).resolve()
    slug, label, root = scope_for(start)
    proj = scope_dir(start, False)

    have_global = (GLOBAL_DIR / "MEMORY.md").exists()
    have_proj = (proj / "MEMORY.md").exists()
    pending = _pending_files(proj)

    if not have_global and not have_proj and not pending:
        print(
            f"NO MEMORY YET for this project ({label}).\n"
            f"Would live at: {proj}\n"
            "Nothing to restore — this is a fresh start."
        )
        return 0

    out: list[str] = [f"# Restored context for {label} ({root})", ""]

    def _digest(path: Path) -> None:
        # A file whose sections are all still empty carries nothing but its own
        # title; printing that just invites the reader to wonder what they missed.
        body = _strip_scaffolding(_read(path))
        if body and "## " in body:
            out.extend([body, ""])

    if have_global:
        _digest(GLOBAL_DIR / "MEMORY.md")
    if have_proj:
        _digest(proj / "MEMORY.md")

    if not args.brief:
        recent = _tail_entries(_read(proj / "log.md"), args.log_entries)
        if recent:
            out += [f"# Recent sessions (last {args.log_entries})", "", recent, ""]

    if pending:
        out += [
            f"# {len(pending)} session(s) captured automatically, not yet distilled",
            "",
        ]
        if args.brief:
            out += [
                "Run `pmem.py pending` to read them:",
                "",
            ] + [f"- {f.stem[:8]}" for f in pending]
        else:
            out += [
                "Fold these into the log (and MEMORY.md if enough has accumulated),",
                "then run `pmem.py clear-pending`. Raw capture follows:",
                "",
            ] + [_read(f).rstrip() + "\n" for f in pending]

    text = "\n".join(out)
    print(text)
    print(f"\n<!-- restored ~{_tokens(text)} tokens -->", file=sys.stderr)
    return 0


def cmd_log(args) -> int:
    start = Path(args.dir).resolve()
    d = ensure_scope(start, args.use_global)

    if args.file:
        body = _read(Path(args.file)).strip()
    else:
        body = sys.stdin.read().strip()

    if not body:
        print("refusing to write an empty log entry", file=sys.stderr)
        return 1

    title = args.title or "session"
    entry = f"\n## {_today()} — {title}\n\n{body}\n"

    log = d / "log.md"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(entry)

    try:
        meta = json.loads(_read(d / "meta.json") or "{}")
    except json.JSONDecodeError:
        meta = {}
    _bump_meta(d, sessions=int(meta.get("sessions", 0)) + 1)

    print(f"appended entry to {log}")
    return 0


def _file_stats(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "lines": 0, "words": 0}
    text = _read(path)
    return {
        "exists": True,
        "lines": len(text.splitlines()),
        "words": len(text.split()),
        "entries": len(re.findall(r"^## ", text, flags=re.M)),
    }


def cmd_stats(args) -> int:
    start = Path(args.dir).resolve()
    slug, label, _ = scope_for(start)
    proj = scope_dir(start, False)

    mem = _file_stats(proj / "MEMORY.md")
    log = _file_stats(proj / "log.md")
    glob = _file_stats(GLOBAL_DIR / "MEMORY.md")
    pending = _pending_files(proj)

    restore_cost = _tokens(
        _strip_scaffolding(_read(GLOBAL_DIR / "MEMORY.md"))
        + _strip_scaffolding(_read(proj / "MEMORY.md"))
        + _tail_entries(_read(proj / "log.md"), 3)
        + "".join(_read(f) for f in pending)
    )

    needs = []
    if len(pending) or log.get("entries", 0) >= CONSOLIDATE_AFTER:
        needs.append(
            f"{len(pending)} pending capture(s), {log.get('entries', 0)} log entries "
            f"— distil the captures into log entries; rewrite MEMORY.md once "
            f"{CONSOLIDATE_AFTER}+ entries have built up, then `pmem.py clear-pending`"
        )
    if mem["lines"] > MEMORY_LINE_BUDGET:
        needs.append(
            f"MEMORY.md is {mem['lines']} lines (budget {MEMORY_LINE_BUDGET}) "
            "— merge duplicates and drop resolved items on this save"
        )
    if log["lines"] > LOG_LINE_BUDGET or log.get("entries", 0) > LOG_ENTRY_BUDGET:
        needs.append(
            f"log.md is {log['lines']} lines / {log.get('entries', 0)} entries "
            "— fold the old entries into MEMORY.md, then run `pmem.py archive`"
        )

    print(
        json.dumps(
            {
                "label": label,
                "mode": "repo" if repo_scope_dir(start) else "home",
                "scope_dir": str(proj),
                "memory": mem,
                "log": log,
                "global_memory": glob,
                "pending_captures": len(pending),
                "restore_cost_tokens": restore_cost,
                "needs_compaction": needs,
            },
            indent=2,
        )
    )
    return 0


def cmd_list(args) -> int:
    if not PROJECTS_DIR.exists():
        print("no project memories yet")
        return 0

    rows = []
    for d in sorted(PROJECTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        try:
            meta = json.loads(_read(d / "meta.json") or "{}")
        except json.JSONDecodeError:
            meta = {}
        mem = _file_stats(d / "MEMORY.md")
        rows.append(
            {
                "label": meta.get("label", d.name),
                "slug": d.name,
                "updated": meta.get("updated", "?"),
                "sessions": meta.get("sessions", 0),
                "memory_lines": mem["lines"],
                "project_root": meta.get("project_root"),
            }
        )

    rows.sort(key=lambda r: r["updated"], reverse=True)
    print(json.dumps(rows, indent=2))
    return 0


def cmd_backup(args) -> int:
    start = Path(args.dir).resolve()
    d = ensure_scope(start, args.use_global)
    src = d / "MEMORY.md"
    backups = d / "backups"
    backups.mkdir(exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest = backups / f"MEMORY-{stamp}.md"
    # Two saves inside the same second would otherwise land on the same name and
    # silently overwrite the older one — the exact thing backups exist to prevent.
    n = 2
    while dest.exists():
        dest = backups / f"MEMORY-{stamp}-{n}.md"
        n += 1
    shutil.copy2(src, dest)

    existing = sorted(backups.glob("MEMORY-*.md"))
    for old in existing[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)

    print(f"backed up to {dest}")
    return 0


def cmd_archive(args) -> int:
    start = Path(args.dir).resolve()
    d = ensure_scope(start, args.use_global)
    log = d / "log.md"

    label = json.loads(_read(d / "meta.json") or "{}").get("label", d.name)
    archive = d / "archive"
    archive.mkdir(exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest = archive / f"log-{stamp}.md"
    shutil.move(str(log), str(dest))
    log.write_text(LOG_TEMPLATE.format(label=label), encoding="utf-8")

    print(
        f"archived to {dest} and started a fresh log.\n"
        "Make sure anything durable from those entries is already in MEMORY.md."
    )
    return 0


# --------------------------------------------------------------------------- #
# automatic capture (runs from the SessionEnd hook — no model involved)
# --------------------------------------------------------------------------- #

def _text_of(message) -> str:
    """Pull human-readable text out of a transcript message, ignoring tool
    traffic and thinking blocks — those are bulk without much durable signal."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _clean(text: str) -> str:
    text = SYS_REMINDER_RE.sub("", text)
    text = CMD_TAG_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split()) if len(text) > limit else text
    return text if len(text) <= limit else text[:limit].rstrip() + " […]"


def extract_transcript(path: Path) -> dict:
    """Reduce a transcript JSONL to the parts worth remembering."""
    turns: list[str] = []
    files: list[str] = []
    cmds: list[str] = []
    last_reply = ""
    first_ts = last_ts = None
    session_id = cwd = None

    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            session_id = d.get("sessionId") or session_id
            cwd = d.get("cwd") or cwd
            ts = d.get("timestamp")
            if ts:
                first_ts = first_ts or ts
                last_ts = ts

            if d.get("isSidechain"):
                continue
            kind = d.get("type")

            if kind == "user" and not d.get("isMeta"):
                text = _clean(_text_of(d.get("message")))
                if text:
                    turns.append(_truncate(text, MAX_TURN_CHARS))

            elif kind == "assistant":
                message = d.get("message")
                text = _clean(_text_of(message))
                if text:
                    last_reply = text
                content = message.get("content") if isinstance(message, dict) else None
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name")
                    args = block.get("input") or {}
                    if not isinstance(args, dict):
                        continue
                    if name in ("Write", "Edit", "NotebookEdit"):
                        path_arg = args.get("file_path")
                        if path_arg and path_arg not in files:
                            files.append(path_arg)
                    elif name == "Bash":
                        cmd = (args.get("command") or "").strip()
                        first = cmd.splitlines()[0] if cmd else ""
                        if first and INTERESTING_CMD_RE.search(first) and first not in cmds:
                            cmds.append(_truncate(first, 160))

    return {
        "session_id": session_id,
        "cwd": cwd,
        "started": first_ts,
        "ended": last_ts,
        "turns": turns[:MAX_TURNS],
        "files": files[:MAX_FILES],
        "commands": cmds[:MAX_CMDS],
        "last_reply": _truncate(last_reply, 600),
    }


def render_capture(data: dict) -> str:
    out = [
        f"### session {(data.get('session_id') or '?')[:8]} "
        f"({(data.get('started') or '?')[:10]})",
        "",
        "**What the user asked for, in their words:**",
        "",
    ]
    for turn in data.get("turns", []):
        # A pasted stack trace or snippet keeps its newlines; without indenting
        # the continuation, the bullet list silently breaks apart.
        out.append("- " + turn.replace("\n", "\n  "))
    if data.get("files"):
        out += ["", "**Files touched:** " + ", ".join(f"`{f}`" for f in data["files"])]
    if data.get("commands"):
        out += ["", "**Notable commands:**"] + [f"- `{c}`" for c in data["commands"]]
    if data.get("last_reply"):
        out += ["", "**Ended with:** " + data["last_reply"]]
    return "\n".join(out) + "\n"


def cmd_capture(args) -> int:
    """Invoked by the SessionEnd hook with the hook payload on stdin.

    Deliberately silent and always exit 0: a memory tool that makes noise or
    fails loudly while someone is closing their terminal is worse than one that
    occasionally misses a session.
    """
    payload = {}
    if not args.transcript:
        try:
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, OSError):
            payload = {}

    transcript = args.transcript or payload.get("transcript_path")
    if not transcript or not Path(transcript).exists():
        return 0

    start = Path(payload.get("cwd") or args.dir or ".").resolve()
    if os.environ.get("PREVIOUS_AUTO", "1") == "0":
        return 0

    data = extract_transcript(Path(transcript))
    if not data.get("turns"):
        return 0  # nothing a human actually said; not worth a file

    d = ensure_scope(start, False)
    pending = d / "pending"
    pending.mkdir(exist_ok=True)

    sid = (data.get("session_id") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    (pending / f"{sid}.md").write_text(render_capture(data), encoding="utf-8")
    return 0


def _section_items(md: str, heading: str) -> int:
    """Count bullets under a `## heading`, for the one-line SessionStart hint."""
    match = re.search(
        rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", md, re.M | re.S
    )
    if not match:
        return 0
    return len(re.findall(r"^\s*[-*] ", match.group(1), re.M))


def cmd_hint(args) -> int:
    """SessionStart hook: print at most one line saying memory exists.

    SessionStart is one of the few events whose stdout reaches the model, which
    makes it tempting to inject the whole digest here. That would put the full
    restore cost on every session, including the ones that have nothing to do
    with the remembered work — so this only ever prints a pointer, and loading
    stays an explicit `/previous`.
    """
    if os.environ.get("PREVIOUS_AUTO", "1") == "0":
        return 0

    payload = {}
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        payload = {}

    start = Path(payload.get("cwd") or args.dir or ".").resolve()
    try:
        slug, label, _ = scope_for(start)
    except OSError:
        return 0

    proj = scope_dir(start, False)
    memory = _strip_scaffolding(_read(proj / "MEMORY.md"))
    pending = _pending_files(proj)
    if not memory and not pending:
        return 0  # nothing remembered here; stay out of the way

    bits = []
    threads = _section_items(memory, "Open threads")
    if threads:
        bits.append(f"{threads} open thread{'s' * (threads != 1)}")
    if pending:
        bits.append(f"{len(pending)} new session{'s' * (len(pending) != 1)} captured")
    if not bits:
        bits.append("digest available")

    cost = _tokens(memory + _tail_entries(_read(proj / "log.md"), 3))
    print(
        f"[previous] Memory for {label} — {', '.join(bits)}. "
        f"Run /previous to load it (~{cost} tokens)."
    )
    return 0


def _pending_files(d: Path) -> list[Path]:
    pending = d / "pending"
    return sorted(pending.glob("*.md")) if pending.exists() else []


def cmd_pending(args) -> int:
    start = Path(args.dir).resolve()
    d = scope_dir(start, False)
    files = _pending_files(d)
    if not files:
        print("no pending captures")
        return 0
    print(f"# {len(files)} captured session(s) awaiting distillation\n")
    for f in files:
        print(_read(f).rstrip())
        print()
    return 0


def cmd_clear_pending(args) -> int:
    start = Path(args.dir).resolve()
    d = scope_dir(start, False)
    files = _pending_files(d)
    for f in files:
        f.unlink(missing_ok=True)
    print(f"cleared {len(files)} pending capture(s)")
    return 0


REPO_GITIGNORE = """# Transient — regenerated on demand, noisy in diffs.
backups/
archive/
"""


def cmd_use_repo(args) -> int:
    """Move this project's memory into the repo so it survives an ephemeral host.

    Claude Code on the web clones fresh and reclaims the container afterwards,
    so home-directory memory never outlives a session there. Committed repo
    memory does.
    """
    start = Path(args.dir).resolve()
    root = project_root(start)
    dest = root / ".claude" / "previous"

    if dest.is_dir():
        print(f"already using repo-local memory at {dest}")
        return 0

    _, label, _ = scope_for(start)
    slug, _, _ = scope_for(start)
    home_scope = PROJECTS_DIR / slug

    dest.mkdir(parents=True, exist_ok=True)
    moved = False
    if home_scope.is_dir():
        for item in home_scope.iterdir():
            if item.name in ("backups", "archive"):
                continue
            target = dest / item.name
            if not target.exists():
                shutil.move(str(item), str(target))
                moved = True

    (dest / ".gitignore").write_text(REPO_GITIGNORE, encoding="utf-8")
    ensure_scope(start, False)

    rel = dest.relative_to(root)
    print(
        f"{'Migrated' if moved else 'Created'} repo-local memory at {rel}/\n"
        f"  label: {label}\n\n"
        "It only persists if you commit it — an ephemeral container discards\n"
        "anything uncommitted when the session ends:\n\n"
        f"  git add {rel} && git commit -m 'Add project memory'\n\n"
        "Note this puts memory under version control, visible to anyone with\n"
        "repo access. To go back, move the directory away:\n"
        f"  mv {rel} ~/.claude/previous/projects/{slug}"
    )
    return 0


def _hook_command(sub: str, repo_mode: bool) -> str:
    """The shell command a hook entry runs.

    Repo settings get committed and read back on another machine, often at a
    different path with a different interpreter, so both halves must stay
    portable. A local install is only ever read by this machine, so it pins the
    exact interpreter known to work — which may not be called `python3`.
    """
    if repo_mode:
        return (
            "python3 ${CLAUDE_PROJECT_DIR}/.claude/skills/previous"
            f"/scripts/pmem.py {sub}"
        )
    script = Path(__file__).resolve()
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {sub}"


def die_shape(key: str, found: str, settings: Path) -> None:
    print(
        f"{settings} has '{key}' as {found}, which is not a shape this "
        "understands.\nFix it by hand (or move the file aside) and re-run — "
        "refusing to overwrite settings that may hold your own hooks.",
        file=sys.stderr,
    )


def cmd_install_hook(args) -> int:
    """Register the SessionEnd hook in ~/.claude/settings.json, preserving
    whatever is already configured there."""
    # On an ephemeral host $HOME is discarded between sessions, so hooks have to
    # live in the repo's own settings to survive — those are committed and read
    # back on the next clone.
    if args.repo:
        settings = project_root(Path(args.dir).resolve()) / ".claude" / "settings.json"
    else:
        settings = Path.home() / ".claude" / "settings.json"

    if args.print_only:
        # Some setups deny an agent write access to settings.json. Rather than
        # leave the automation unreachable, emit the block for a human to paste.
        entries = {
            event: [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": _hook_command(sub, args.repo),
                            "timeout": 30,
                        }
                    ],
                }
            ]
            for event, sub in (("SessionEnd", "capture"), ("SessionStart", "hint"))
        }
        print(f"# Merge into the \"hooks\" object of {settings}\n")
        print(json.dumps({"hooks": entries}, indent=2))
        return 0

    settings.parent.mkdir(parents=True, exist_ok=True)

    try:
        conf = json.loads(_read(settings) or "{}")
    except json.JSONDecodeError:
        print(
            f"{settings} is not valid JSON — fix or move it first; "
            "refusing to overwrite a file I can't parse.",
            file=sys.stderr,
        )
        return 1

    if settings.exists():
        shutil.copy2(settings, settings.with_suffix(".json.bak"))

    script = Path(__file__).resolve()

    # A key holding null carries no configuration, so replacing it loses
    # nothing. A key holding the wrong *type* may well hold someone's hooks in
    # a shape we don't understand — bail rather than silently discard them.
    if conf.get("hooks") is None:
        conf["hooks"] = {}
    if not isinstance(conf["hooks"], dict):
        die_shape("hooks", type(conf["hooks"]).__name__, settings)
        return 1
    hooks = conf["hooks"]

    results = []

    for event, sub in (("SessionEnd", "capture"), ("SessionStart", "hint")):
        command = _hook_command(sub, args.repo)
        if hooks.get(event) is None:
            hooks[event] = []
        if not isinstance(hooks[event], list):
            die_shape(f"hooks.{event}", type(hooks[event]).__name__, settings)
            return 1
        groups = hooks[event]

        existing = None
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            for h in group["hooks"]:
                if not isinstance(h, dict):
                    continue
                if f"pmem.py {sub}" in str(h.get("command", "")):
                    existing = h
        if existing is not None:
            existing["command"] = command
            results.append(f"  {event}: refreshed")
        else:
            groups.append(
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": command, "timeout": 30}],
                }
            )
            results.append(f"  {event}: installed ({command})")

    settings.write_text(json.dumps(conf, indent=2), encoding="utf-8")
    print(
        "\n".join([f"updated {settings}"] + results)
        + "\n\nSessions are captured on exit, and new sessions get a one-line"
        "\npointer when memory exists. Set PREVIOUS_AUTO=0 to pause both."
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="pmem", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, with_global=True):
        sp.add_argument("--dir", default=".", help="directory to resolve scope from")
        if with_global:
            sp.add_argument(
                "--global",
                dest="use_global",
                action="store_true",
                help="operate on the cross-project global layer",
            )
        return sp

    common(sub.add_parser("path", help="show where memory lives")).set_defaults(
        func=cmd_path
    )

    sp = common(sub.add_parser("show", help="print global + project memory"), False)
    sp.add_argument("--log-entries", type=int, default=3)
    sp.add_argument(
        "--brief",
        action="store_true",
        help="digest only — skip log entries and raw captures",
    )
    sp.set_defaults(func=cmd_show, use_global=False)

    sp = common(sub.add_parser("capture", help="SessionEnd hook entry point"), False)
    sp.add_argument("--transcript", help="transcript path (else read hook JSON on stdin)")
    sp.set_defaults(func=cmd_capture, use_global=False)

    common(sub.add_parser("hint", help="SessionStart one-line pointer"), False).set_defaults(
        func=cmd_hint, use_global=False
    )
    common(sub.add_parser("pending", help="show captures awaiting distillation"), False).set_defaults(
        func=cmd_pending, use_global=False
    )
    common(sub.add_parser("clear-pending", help="drop distilled captures"), False).set_defaults(
        func=cmd_clear_pending, use_global=False
    )
    common(sub.add_parser("use-repo", help="keep memory in the repo, not $HOME"), False).set_defaults(
        func=cmd_use_repo, use_global=False
    )
    sp = sub.add_parser("install-hook", help="register the session hooks")
    sp.add_argument("--dir", default=".", help="directory to resolve the repo from")
    sp.add_argument(
        "--repo",
        action="store_true",
        help="write to the repo's .claude/settings.json instead of $HOME "
        "(for ephemeral hosts, where $HOME does not persist)",
    )
    sp.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="print the JSON to paste by hand instead of writing anything",
    )
    sp.set_defaults(func=cmd_install_hook, use_global=False, print_only=False)

    sp = common(sub.add_parser("log", help="append a session entry"))
    sp.add_argument("--title", help="short entry title")
    sp.add_argument("--file", help="read body from file instead of stdin")
    sp.set_defaults(func=cmd_log)

    common(sub.add_parser("stats", help="sizes and compaction pressure"), False).set_defaults(
        func=cmd_stats, use_global=False
    )

    sub.add_parser("list", help="list all remembered projects").set_defaults(
        func=cmd_list
    )

    common(sub.add_parser("backup", help="snapshot MEMORY.md")).set_defaults(
        func=cmd_backup
    )
    common(sub.add_parser("archive", help="rotate log.md")).set_defaults(
        func=cmd_archive
    )

    args = p.parse_args()
    if not hasattr(args, "use_global"):
        args.use_global = False
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
