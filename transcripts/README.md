# Transcripts

Every conversation with the development agents is archived here, raw and unedited, abandoned attempts included.

- `raw/<session_id>.jsonl`: raw export of the Claude Code sessions, copied automatically on every turn by a hook (`.claude/settings.json` → `scripts/sync_transcripts.sh`). The mechanism is external to the agent, which can neither forget nor bypass it.
- `<session_id>.md`: readable rendering of the matching raw file (`scripts/render_transcript.py`).

Nothing in this folder is edited by hand.
