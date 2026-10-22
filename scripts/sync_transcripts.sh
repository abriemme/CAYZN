#!/usr/bin/env bash
# Copy the current Claude Code session transcript into transcripts/.
# Called by the Stop and SessionEnd hooks. Must never block the agent: always exit 0.
set -u

input=$(cat)
session_id=$(printf '%s' "$input" | jq -r '.session_id // empty')
transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty')

root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
dest="$root/transcripts/raw"

[ -n "$session_id" ] && [ -f "$transcript" ] || exit 0

mkdir -p "$dest"
cp "$transcript" "$dest/$session_id.jsonl"

# Subagent sessions, if Claude Code stores them in a folder named after the session
sub_dir="$(dirname "$transcript")/$session_id"
[ -d "$sub_dir" ] && cp -r "$sub_dir" "$dest/" 2>/dev/null

# Markdown rendering, if the script exists
[ -f "$root/scripts/render_transcript.py" ] && \
  python3 "$root/scripts/render_transcript.py" "$dest/$session_id.jsonl" \
    > "$root/transcripts/$session_id.md" 2>/dev/null

exit 0
