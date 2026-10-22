#!/usr/bin/env python3
"""Render a Claude Code transcript (JSONL) as chronological markdown.

Usage: render_transcript.py <session.jsonl> > <session.md>

Nothing is filtered or reworded: every user message, every reply, every tool
call and every (truncated) result appears in order. Purely technical entries
(snapshots, system attachments, hooks) are omitted since they carry no exchange.
"""

import json
import sys
from datetime import datetime

RESULT_MAX_LINES = 15
RESULT_MAX_CHARS = 1500
THINKING_MAX_CHARS = 600

# Parameter shown on the line summarising a tool call, per tool.
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
        text += "\n… (truncated)"
    return text


def fence(text, lang=""):
    # Prevent a ``` in the content from breaking the block.
    delim = "````" if "```" in text else "```"
    return f"{delim}{lang}\n{text}\n{delim}"


def tool_use_line(block):
    name = block.get("name", "?")
    params = block.get("input") or {}
    key = PRIMARY_PARAM.get(name)
    detail = params.get(key) if key else None
    if detail is None and params:
        # First scalar parameter, for lack of a better one.
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
                out.append(f"> unreadable JSON line: `{raw[:80]}…`\n")
                continue

            etype = entry.get("type")

            if not header_done and entry.get("sessionId") and entry.get("timestamp"):
                out.append(f"# Session `{entry['sessionId']}`\n")
                out.append(f"- Start: {fmt_time(entry.get('timestamp'))}")
                if entry.get("cwd"):
                    out.append(f"- Directory: `{entry['cwd']}`")
                if entry.get("gitBranch"):
                    out.append(f"- Branch: `{entry['gitBranch']}`")
                if entry.get("version"):
                    out.append(f"- Claude Code : {entry['version']}")
                out.append("")
                header_done = True

            # Failing hooks are real session events: keep them.
            if etype == "attachment":
                att = entry.get("attachment") or {}
                if att.get("type") == "hook_non_blocking_error":
                    err = (att.get("stderr") or "").strip()
                    out.append(
                        f"⚠️ Hook `{att.get('hookName', '?')}` failed (exit {att.get('exitCode')}):\n"
                    )
                    out.append(fence(truncate(err, max_lines=5, max_chars=400)) + "\n")
                continue

            if etype not in ("user", "assistant"):
                continue

            msg = entry.get("message") or {}
            content = msg.get("content")
            ts = fmt_time(entry.get("timestamp"))
            side = " *(subagent)*" if entry.get("isSidechain") else ""

            if etype == "user":
                if isinstance(content, str):
                    user_turn += 1
                    out.append(f"\n---\n\n## 👤 User — turn {user_turn}{side}\n")
                    out.append(f"*{ts}*\n")
                    out.append(content.strip() + "\n")
                elif isinstance(content, list):
                    for block in content:
                        btype = block.get("type")
                        if btype == "text":
                            user_turn += 1
                            out.append(
                                f"\n---\n\n## 👤 User — turn {user_turn}{side}\n"
                            )
                            out.append(f"*{ts}*\n")
                            out.append(block.get("text", "").strip() + "\n")
                        elif btype == "tool_result":
                            label = (
                                "❌ Result (error)"
                                if block.get("is_error")
                                else "↩️ Result"
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
                        out.append("💭 *Thinking:*\n")
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
