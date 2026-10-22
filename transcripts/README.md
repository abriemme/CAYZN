# Transcripts

Toutes les conversations avec les agents de développement sont archivées ici, brutes et non éditées, tentatives abandonnées comprises.

- `raw/<session_id>.jsonl` : export brut des sessions Claude Code, copié automatiquement à chaque tour par un hook (`.claude/settings.json` → `scripts/sync_transcripts.sh`). Le mécanisme est externe à l'agent, qui ne peut ni l'oublier ni le contourner.
- `<session_id>.md` : rendu lisible du fichier brut correspondant (`scripts/render_transcript.py`).

Rien n'est édité à la main dans ce dossier.
