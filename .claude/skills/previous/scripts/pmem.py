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


def scope_dir(start: Path, use_global: bool) -> Path:
    if use_global:
        return GLOBAL_DIR
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
    proj = PROJECTS_DIR / slug

    have_global = (GLOBAL_DIR / "MEMORY.md").exists()
    have_proj = (proj / "MEMORY.md").exists()

    if not have_global and not have_proj:
        print(
            f"NO MEMORY YET for this project ({label}).\n"
            f"Would live at: {proj}\n"
            "Nothing to restore — this is a fresh start."
        )
        return 0

    out: list[str] = [
        f"# Restored context for {label}",
        f"_Project root: {root}_",
        "",
    ]

    if have_global:
        out += ["---", "", _read(GLOBAL_DIR / "MEMORY.md").rstrip(), ""]

    if have_proj:
        out += ["---", "", _read(proj / "MEMORY.md").rstrip(), ""]
        recent = _tail_entries(_read(proj / "log.md"), args.log_entries)
        if recent:
            out += [
                "---",
                "",
                f"# Recent sessions (last {args.log_entries})",
                "",
                recent,
                "",
            ]
    else:
        out += [
            "---",
            "",
            f"No project-specific memory yet for {label} — only the global layer above.",
            "",
        ]

    print("\n".join(out))
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
    proj = PROJECTS_DIR / slug

    mem = _file_stats(proj / "MEMORY.md")
    log = _file_stats(proj / "log.md")
    glob = _file_stats(GLOBAL_DIR / "MEMORY.md")

    needs = []
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
                "scope_dir": str(proj),
                "memory": mem,
                "log": log,
                "global_memory": glob,
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
    sp.set_defaults(func=cmd_show, use_global=False)

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
