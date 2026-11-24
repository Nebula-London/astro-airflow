#!/usr/bin/env bash
# Install this skill for one or more agents (copies the skill folder; re-run after every update).
#   bash scripts/install.sh [cortex] [claude] [opencode] [agents] [all] [--project DIR] [--dry-run] [--force]
#   bash scripts/install.sh --list        # where this skill is installed now, its version, and whether it matches
# Targets (user-level):
#   cortex   ~/.snowflake/cortex/skills/<name>   (Cortex Code)
#   claude   ~/.claude/skills/<name>             (Claude Code; OpenCode also reads this)
#   opencode ~/.config/opencode/skills/<name>    (OpenCode)
#   agents   ~/.agents/skills/<name>             (Agent Skills convention; OpenCode also reads this)
# --project DIR installs into DIR/.claude/skills and DIR/.opencode/skills instead.
# A copy that differs is backed up to <this skill>/dist/backups/ first (older versions used ~/.cache/skill-backups:
# those are moved there too). A copy with a HIGHER version than this
# source is refused (merge it first, or --force): stale copies in different agents are how edits get lost.
# Member data (masters, trackers, answer memory) never lives in the skill folder, so replacing it is safe.
# Claude.ai (web) cannot read local folders: run scripts/package.sh and upload the zip in Settings > Capabilities > Skills.
# Works with the macOS system bash (3.2); needs rsync and any python3 (the skills themselves run on Python 3.14,
# which their launchers fetch into the member's job-search folder with uv).
set -e
src="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
name="$(sed -n 's/^name:[[:space:]]*//p' "$src/SKILL.md" | head -1)"
[ -n "$name" ] || { echo "FAIL: no 'name:' in $src/SKILL.md" >&2; exit 1; }
EXC=(--exclude .git --exclude __pycache__ --exclude dist --exclude .cortex)
ver()  { sed -n 's/^[[:space:]]*version:[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}[[:space:]]*$/\1/p' "$1/SKILL.md" 2>/dev/null | head -1; }
dest() { case "$1" in
  cortex) echo "$HOME/.snowflake/cortex/skills/$name" ;; claude) echo "$HOME/.claude/skills/$name" ;;
  opencode) echo "$HOME/.config/opencode/skills/$name" ;; agents) echo "$HOME/.agents/skills/$name" ;; *) return 1 ;; esac; }
same() { diff -rq -x __pycache__ -x dist -x .git -x .cortex "$1" "$2" >/dev/null 2>&1; }
newer() { [ -n "$1" ] && [ -n "$2" ] && [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)" = "$1" ]; }
usage() { sed -n '2,15p' "$0"; }

proj=""; targets=(); dry=0; force=0
while [ $# -gt 0 ]; do
  case "$1" in
    --project) proj="$2"; shift 2 ;;
    --dry-run) dry=1; shift ;;
    --force) force=1; shift ;;
    all) targets+=(cortex claude opencode agents); shift ;;
    -h|--help) usage; exit 0 ;;
    --list)
      echo "$name  source: $src  v$(ver "$src")"
      for t in cortex claude opencode agents; do
        d="$(dest $t)"
        if [ -f "$d/SKILL.md" ]; then
          s="DIFFERS"; same "$src" "$d" && s="same as source"
          printf '  %-8s v%-5s %-15s %s\n' "$t" "$(ver "$d")" "$s" "$d"
        else printf '  %-8s -\n' "$t"; fi
      done; exit 0 ;;
    *) targets+=("$1"); shift ;;
  esac
done
[ ${#targets[@]} -eq 0 ] && [ -z "$proj" ] && { usage; exit 2; }
command -v rsync >/dev/null || { echo "FAIL: rsync is required" >&2; exit 1; }

dests=()
[ -n "$proj" ] && dests=("$proj/.claude/skills/$name" "$proj/.opencode/skills/$name")
for t in ${targets[@]+"${targets[@]}"}; do
  d="$(dest "$t")" || { echo "unknown target: $t (cortex | claude | opencode | agents | all)" >&2; exit 2; }
  dests+=("$d")
done

sv="$(ver "$src")"; stamp="$(date +%Y%m%d-%H%M%S)"; rc=0
for d in "${dests[@]}"; do
  if [ -d "$d" ] && [ "$(cd "$d" && pwd -P)" = "$(cd "$src" && pwd -P)" ]; then echo "skip: $d is the source"; continue; fi
  if [ -d "$d" ] && same "$src" "$d"; then echo "up to date: $d (v$sv)"; continue; fi
  if [ -d "$d" ]; then
    dv="$(ver "$d")"
    if newer "$dv" "$sv" && [ $force = 0 ]; then
      echo "REFUSED: $d is v$dv, newer than this source (v$sv). Merge it into the source, or re-run with --force." >&2
      rc=1; continue
    fi
  fi
  if [ $dry = 1 ]; then
    echo "would install v$sv -> $d"
    rsync -an --delete --itemize-changes "${EXC[@]}" "$src/" "$d/" | sed 's/^/    /' | head -25
    continue
  fi
  if [ -d "$d" ]; then
    old="$HOME/.cache/skill-backups/$name"
    if [ -d "$old" ]; then
      mkdir -p "$src/dist/backups" && { mv "$old"/* "$src/dist/backups/" 2>/dev/null || true; }
      rmdir "$old" 2>/dev/null || true; rmdir "$HOME/.cache/skill-backups" 2>/dev/null || true
      echo "moved old backups from ~/.cache/skill-backups/$name to $src/dist/backups/"
    fi
    b="$src/dist/backups/$(echo "$d" | tr '/' '_')-$stamp"
    mkdir -p "$(dirname "$b")" && rsync -a --exclude __pycache__ "$d/" "$b/" && echo "backup: $b"
  fi
  mkdir -p "$d"
  rsync -a --delete "${EXC[@]}" "$src/" "$d/"
  find "$d" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true   # rsync keeps excluded dirs
  chmod +x "$d"/scripts/*.sh "$d"/scripts/*.py "$d"/scripts/py 2>/dev/null || true
  [ -f "$d/scripts/cv" ] && chmod +x "$d/scripts/cv"
  bad=""; pyc="$(command -v python3.15 || command -v python3.14 || command -v python3.13 || command -v python3)"
  for f in "$d"/scripts/*.py; do
    [ -f "$f" ] || continue
    "$pyc" -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f" 2>/dev/null || bad="$bad $(basename "$f")"
  done
  if [ -n "$bad" ]; then echo "FAIL: $d installed, but these do not parse:$bad" >&2; rc=1
  else echo "installed: $d (v$(ver "$d"))"; fi
done
exit $rc
