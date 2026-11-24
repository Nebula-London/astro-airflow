#!/usr/bin/env bash
# Build dist/<name>.zip for Claude.ai (Settings > Capabilities > Skills > Upload) or any agent that takes a zip.
# The zip holds one top-level folder named after the skill, with SKILL.md inside it.
set -e
src="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
name="$(sed -n 's/^name:[[:space:]]*//p' "$src/SKILL.md" | head -1)"
bash "$src/scripts/check_release.sh" "$src" || { echo "FAIL: not packaged (fix the lines above first)" >&2; exit 1; }
tmp="$(mktemp -d)"; mkdir -p "$tmp/$name" "$src/dist"
rsync -a --exclude .git --exclude __pycache__ --exclude dist --exclude .cortex --exclude .DS_Store "$src/" "$tmp/$name/"
rm -f "$src/dist/$name.zip"; (cd "$tmp" && zip -qr "$src/dist/$name.zip" "$name")
rm -rf "$tmp"; echo "package: $src/dist/$name.zip"; unzip -l "$src/dist/$name.zip" | tail -1
