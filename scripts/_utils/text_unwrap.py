"""
File: scripts/_utils/text_unwrap.py

Description:
  Remove hard line breaks from markdown prose: every wrapped paragraph
  (including list-item continuations) becomes one long line, so
  renderers that soft-wrap — the GitHub issue page above all — wrap it
  themselves.  gh_project_populate.py applies this to the plan file by
  default, so authored ~72-column prose reaches GitHub unwrapped.

  Everything that is not reflowable prose survives byte-for-byte:
  blank lines, headings, horizontal rules and setext underlines,
  tables, blockquotes, fenced code blocks (mermaid, agda, ...),
  indented code blocks, HTML-comment and generated-region marker
  lines, and the plan grammar's line-oriented bold metadata
  (`**Labels:** ...`, `**Milestone:** ...`, `**Repository**: ...`) —
  the engine parses those per line, so they must never merge into a
  neighbor.  The acceptance property, pinned by the tests: a plan file
  parses IDENTICALLY (same milestones, labels, issue ids and label
  sets) before and after unwrapping, and still lints clean; only
  bodies and descriptions reflow.

  Join rule: a break after sentence-ending punctuation becomes two
  spaces (the two-spaces-after-a-period prose style); any other break
  becomes one space.  `unwrap` is a total function and idempotent.

Design Principles:
  - Pure: a single str -> str transform; no I/O (callers use
    file_ops), no exceptions on any input.
  - Line-classification constants are module-level and independently
    testable.
"""
from __future__ import annotations

import re

# ── Line classification ──────────────────────────────────────────────────────

FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d{1,9}[.)])\s+\S")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
HR_OR_SETEXT_RE = re.compile(r"^\s{0,3}(?:-{3,}|_{3,}|\*{3,}|={3,})\s*$")

# `**Labels:** ...` / `**Milestone:** ...` / `**Repository**: ...` — the
# plan grammar is line-oriented for these, so such a line always starts
# its own paragraph; its own wrapped VALUE may still join onto it.
BOLD_META_RE = re.compile(r"^\s{0,3}\*\*[^*\n]+(?:\*\*\s*:|:\*\*)")

_SENTENCE_END_RE = re.compile(r"[.!?][\"')\]`*_]*$")


def is_structural(line: str) -> bool:
    """A line that must never be merged into a neighboring one."""
    stripped = line.lstrip()
    return bool(
        HEADING_RE.match(line)
        or HR_OR_SETEXT_RE.match(line)
        or stripped.startswith(("|", ">", "<!--", "-->"))
    )


def _joiner(previous: str) -> str:
    """Two spaces after a sentence end, one space otherwise."""
    return "  " if _SENTENCE_END_RE.search(previous) else " "


def _flush(paragraph: list[str], out: list[str]) -> None:
    if not paragraph:
        return
    joined = paragraph[0].rstrip()
    for continuation in paragraph[1:]:
        joined += _joiner(joined) + continuation.strip()
    out.append(joined)
    paragraph.clear()


def unwrap(text: str) -> str:
    """Reflow markdown prose onto single lines; leave structure alone."""
    out: list[str] = []
    paragraph: list[str] = []
    fence_close: re.Pattern[str] | None = None

    for line in text.splitlines():
        if fence_close is not None:
            out.append(line)
            if fence_close.match(line):
                fence_close = None
            continue

        opening = FENCE_RE.match(line)
        if opening:
            _flush(paragraph, out)
            out.append(line)
            char, count = opening.group(2)[0], len(opening.group(2))
            fence_close = re.compile(rf"^\s*{re.escape(char)}{{{count},}}\s*$")
            continue

        if not line.strip():
            _flush(paragraph, out)
            out.append(line)
            continue

        if paragraph:
            if is_structural(line):
                _flush(paragraph, out)
                out.append(line)
            elif LIST_ITEM_RE.match(line) or BOLD_META_RE.match(line):
                _flush(paragraph, out)
                paragraph.append(line)
            else:
                paragraph.append(line)
            continue

        # Fresh block position: four-space-indented lines are code
        # (list continuations never reach here — their paragraph is
        # still open when they arrive).
        if line.startswith("    "):
            out.append(line)
        elif is_structural(line):
            out.append(line)
        else:
            paragraph.append(line)

    _flush(paragraph, out)
    result = "\n".join(out)
    if text.endswith("\n"):
        result += "\n"
    return result
