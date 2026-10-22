#!/usr/bin/env bash
# Copie le transcript de la session Claude Code courante dans transcripts/.
# Appelé par les hooks Stop et SessionEnd. Ne doit jamais bloquer l'agent : exit 0 quoi qu'il arrive.
set -u

input=$(cat)
session_id=$(printf '%s' "$input" | jq -r '.session_id // empty')
transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty')

root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
dest="$root/transcripts/raw"

[ -n "$session_id" ] && [ -f "$transcript" ] || exit 0

mkdir -p "$dest"
cp "$transcript" "$dest/$session_id.jsonl"

# Les sessions de sous-agents, si Claude Code les range dans un dossier au nom de la session
sub_dir="$(dirname "$transcript")/$session_id"
[ -d "$sub_dir" ] && cp -r "$sub_dir" "$dest/" 2>/dev/null

# Rendu markdown, si le script existe déjà
[ -f "$root/scripts/render_transcript.py" ] && \
  python3 "$root/scripts/render_transcript.py" "$dest/$session_id.jsonl" \
    > "$root/transcripts/$session_id.md" 2>/dev/null

exit 0
