#!/usr/bin/env python3
"""Rend un transcript Claude Code (JSONL) en markdown chronologique.

Usage : render_transcript.py <session.jsonl> > <session.md>

Rien n'est filtré ni reformulé : chaque message utilisateur, chaque réponse,
chaque appel d'outil et chaque résultat (tronqué) apparaissent dans l'ordre.
Les entrées purement techniques (snapshots, attachements système, hooks)
sont omises car elles ne portent aucun échange.
"""

import json
import sys
from datetime import datetime

RESULT_MAX_LINES = 15
RESULT_MAX_CHARS = 1500
THINKING_MAX_CHARS = 600

# Paramètre à afficher sur la ligne résumant un appel d'outil, par outil.
PRIMARY_PARAM = {
    "Bash": "description",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
    "WebFetch": "url",
    "WebSearch": "query",
    "Agent": "description",
    "Skill": "skill",
    "ToolSearch": "query",
}


def fmt_time(ts):
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return ts


def truncate(text, max_lines=RESULT_MAX_LINES, max_chars=RESULT_MAX_CHARS):
    text = text.rstrip()
    lines = text.splitlines()
    cut = False
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        cut = True
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars]
        cut = True
    if cut:
        text += "\n… (tronqué)"
    return text


def fence(text, lang=""):
    # Évite qu'un ``` dans le contenu ne casse le bloc.
    delim = "````" if "```" in text else "```"
    return f"{delim}{lang}\n{text}\n{delim}"


def tool_use_line(block):
    name = block.get("name", "?")
    params = block.get("input") or {}
    key = PRIMARY_PARAM.get(name)
    detail = params.get(key) if key else None
    if detail is None and params:
        # Premier paramètre scalaire, faute de mieux.
        for v in params.values():
            if isinstance(v, (str, int, float)):
                detail = v
                break
    detail = str(detail).replace("\n", " ") if detail is not None else ""
    if len(detail) > 200:
        detail = detail[:200] + "…"
    return f"🔧 **{name}** — `{detail}`" if detail else f"🔧 **{name}**"


def tool_result_text(block):
    content = block.get("content")
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif isinstance(c, dict) and c.get("type") == "image":
                parts.append("[image]")
        content = "\n".join(parts)
    if content is None:
        content = ""
    return str(content)


def render(path):
    out = []
    header_done = False
    user_turn = 0

    with open(path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                out.append(f"> ligne JSON illisible : `{raw[:80]}…`\n")
                continue

            etype = entry.get("type")

            if not header_done and entry.get("sessionId") and entry.get("timestamp"):
                out.append(f"# Session `{entry['sessionId']}`\n")
                out.append(f"- Début : {fmt_time(entry.get('timestamp'))}")
                if entry.get("cwd"):
                    out.append(f"- Répertoire : `{entry['cwd']}`")
                if entry.get("gitBranch"):
                    out.append(f"- Branche : `{entry['gitBranch']}`")
                if entry.get("version"):
                    out.append(f"- Claude Code : {entry['version']}")
                out.append("")
                header_done = True

            # Les hooks qui échouent sont de vrais événements de la session : on les garde.
            if etype == "attachment":
                att = entry.get("attachment") or {}
                if att.get("type") == "hook_non_blocking_error":
                    err = (att.get("stderr") or "").strip()
                    out.append(
                        f"⚠️ Hook `{att.get('hookName', '?')}` en erreur (exit {att.get('exitCode')}) :\n"
                    )
                    out.append(fence(truncate(err, max_lines=5, max_chars=400)) + "\n")
                continue

            if etype not in ("user", "assistant"):
                continue

            msg = entry.get("message") or {}
            content = msg.get("content")
            ts = fmt_time(entry.get("timestamp"))
            side = " *(sous-agent)*" if entry.get("isSidechain") else ""

            if etype == "user":
                if isinstance(content, str):
                    user_turn += 1
                    out.append(f"\n---\n\n## 👤 Utilisateur — tour {user_turn}{side}\n")
                    out.append(f"*{ts}*\n")
                    out.append(content.strip() + "\n")
                elif isinstance(content, list):
                    for block in content:
                        btype = block.get("type")
                        if btype == "text":
                            user_turn += 1
                            out.append(
                                f"\n---\n\n## 👤 Utilisateur — tour {user_turn}{side}\n"
                            )
                            out.append(f"*{ts}*\n")
                            out.append(block.get("text", "").strip() + "\n")
                        elif btype == "tool_result":
                            label = (
                                "❌ Résultat (erreur)"
                                if block.get("is_error")
                                else "↩️ Résultat"
                            )
                            out.append(f"{label}{side}:\n")
                            out.append(fence(truncate(tool_result_text(block))) + "\n")
                continue

            # assistant
            if isinstance(content, str):
                out.append(f"### 🤖 Assistant{side}\n\n*{ts}*\n\n{content.strip()}\n")
                continue
            if not isinstance(content, list):
                continue
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "").strip()
                    if text:
                        out.append(f"### 🤖 Assistant{side}\n\n*{ts}*\n\n{text}\n")
                elif btype == "thinking":
                    thought = (block.get("thinking") or "").strip()
                    if thought:
                        out.append("💭 *Réflexion :*\n")
                        out.append(
                            fence(
                                truncate(
                                    thought, max_lines=12, max_chars=THINKING_MAX_CHARS
                                )
                            )
                            + "\n"
                        )
                elif btype == "tool_use":
                    out.append(tool_use_line(block) + side + "\n")

    return "\n".join(out).rstrip() + "\n"


def main():
    if len(sys.argv) != 2:
        sys.stderr.write(__doc__)
        sys.exit(2)
    sys.stdout.write(render(sys.argv[1]))


if __name__ == "__main__":
    main()
