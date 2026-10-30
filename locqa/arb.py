"""Duplicate-aware ARB (Application Resource Bundle) reader / writer.

Why not plain ``json.load``?
  * ARB files in the wild repeat keys (``"@@comment"`` section markers,
    accidentally duplicated messages).  ``json`` silently keeps only the
    last value, which hides bugs.  We keep every occurrence with its line.
  * When we write a fixed file we want a *minimal diff*: same order, same
    formatting, same comments.  So the writer patches values in place on the
    original text instead of re-serialising the whole document.

Runtime semantics follow Dart's ``jsonDecode`` (used by ``flutter gen-l10n``):
for duplicated keys the **last** occurrence wins.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Entry:
    key: str
    value: object          # str for messages, dict for @metadata
    line: int              # 1-based line number in the file (0 if unknown)
    index: int             # position among top-level members


@dataclass
class ArbFile:
    path: str
    text: str
    entries: List[Entry] = field(default_factory=list)

    # ---- views -----------------------------------------------------------
    @property
    def locale(self) -> Optional[str]:
        v = self.get_raw("@@locale")
        return v if isinstance(v, str) else None

    def get_raw(self, key: str):
        val = None
        for e in self.entries:
            if e.key == key:
                val = e.value
        return val

    @property
    def messages(self) -> Dict[str, str]:
        """key -> message string (last occurrence wins, like Dart)."""
        out: Dict[str, str] = {}
        for e in self.entries:
            if is_message_key(e.key) and isinstance(e.value, str):
                out[e.key] = e.value
        return out

    @property
    def metadata(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for e in self.entries:
            if e.key.startswith("@") and not e.key.startswith("@@") and isinstance(e.value, dict):
                out[e.key[1:]] = e.value
        return out

    def line_of(self, key: str) -> int:
        ln = 0
        for e in self.entries:
            if e.key == key:
                ln = e.line
        return ln

    def duplicates(self) -> Dict[str, List[Entry]]:
        groups: Dict[str, List[Entry]] = {}
        for e in self.entries:
            if e.key.startswith("@@"):
                continue
            groups.setdefault(e.key, []).append(e)
        return {k: v for k, v in groups.items() if len(v) > 1}

    def section_of(self) -> Dict[str, str]:
        """key -> nearest preceding ``@@comment`` value (context for reviewers)."""
        out, cur = {}, ""
        for e in self.entries:
            if e.key == "@@comment" and isinstance(e.value, str):
                cur = e.value
            elif is_message_key(e.key):
                out[e.key] = cur
        return out


def is_message_key(key: str) -> bool:
    return not key.startswith("@")


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

_KEY_LINE_RE = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"\s*:')


def _key_lines(text: str) -> Dict[str, List[int]]:
    """Map each top-level-looking key to the list of line numbers it starts on."""
    res: Dict[str, List[int]] = {}
    depth = 0
    for i, line in enumerate(text.split("\n"), 1):
        m = _KEY_LINE_RE.match(line)
        if m and depth == 1:
            res.setdefault(json.loads('"' + m.group(1) + '"'), []).append(i)
        depth += _brace_delta(line)
    return res


def _brace_delta(line: str) -> int:
    d, in_str, esc = 0, False, False
    for ch in line:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            d += 1
        elif ch == "}":
            d -= 1
    return d


def loads(text: str, path: str = "<string>") -> ArbFile:
    if text.startswith("﻿"):
        text = text[1:]
    holder: List[list] = []

    def hook(pairs):
        holder.append(pairs)
        return dict(pairs)

    try:
        json.loads(text, object_pairs_hook=hook)
    except json.JSONDecodeError as exc:  # pragma: no cover - surfaced to CLI
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    top = holder[-1] if holder else []
    lines = _key_lines(text)
    seen: Dict[str, int] = {}
    entries = []
    for idx, (k, v) in enumerate(top):
        n = seen.get(k, 0)
        seen[k] = n + 1
        ln_list = lines.get(k, [])
        ln = ln_list[n] if n < len(ln_list) else 0
        entries.append(Entry(k, v, ln, idx))
    return ArbFile(path=path, text=text, entries=entries)


def load(path: str | Path) -> ArbFile:
    p = Path(path)
    return loads(p.read_text(encoding="utf-8"), str(p))


# --------------------------------------------------------------------------
# writing (minimal-diff patching)
# --------------------------------------------------------------------------

def dumps_value(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def patch(arb: ArbFile, changes: Dict[str, str],
          add_after: Optional[Dict[str, Optional[str]]] = None,
          metadata: Optional[Dict[str, dict]] = None) -> str:
    """Return new file text.

    changes   : key -> new value for keys that already exist (all duplicate
                occurrences are patched so the runtime value is unambiguous).
    add_after : new_key -> existing key after which to insert it (None = end).
                The value is taken from ``changes``.
    metadata  : key -> dict written/merged as ``"@key": {...}`` right after
                the message line.
    """
    add_after = add_after or {}
    metadata = metadata or {}
    lines = arb.text.split("\n")
    key_lines = _key_lines(arb.text)
    existing_meta = arb.metadata

    # 1) in-place value replacement
    for key, new in changes.items():
        if key in add_after:
            continue
        for ln in key_lines.get(key, []):
            line = lines[ln - 1]
            m = re.match(r'^(\s*"(?:[^"\\]|\\.)*"\s*:\s*)("(?:[^"\\]|\\.)*")(\s*,?\s*)$', line)
            if not m:
                raise ValueError(f"cannot patch multi-line value for {key!r} at line {ln}")
            lines[ln - 1] = m.group(1) + dumps_value(new) + m.group(3)

    # 2) insertions + metadata, processed bottom-up so line numbers stay valid
    inserts: Dict[int, List[str]] = {}

    def indent_of(ln: int) -> str:
        return re.match(r"^(\s*)", lines[ln - 1]).group(1) or "  "

    for key, anchor in add_after.items():
        val = changes[key]
        if anchor and key_lines.get(anchor):
            ln = key_lines[anchor][-1]
            # skip anchor's metadata block if present
            ln = _end_of_member(lines, ln)
        else:
            ln = _last_member_line(lines)
        ind = indent_of(ln)
        block = [f"{ind}{dumps_value(key)}: {dumps_value(val)},"]
        if key in metadata:
            block.append(f"{ind}{dumps_value('@' + key)}: {json.dumps(metadata[key], ensure_ascii=False)},")
        inserts.setdefault(ln, []).extend(block)

    for key, meta in metadata.items():
        if key in add_after:
            continue
        if key in existing_meta:
            # merge into existing metadata block: replace whole block
            start = key_lines.get("@" + key, [None])[-1]
            if start is None:
                continue
            end = _end_of_member(lines, start)
            merged = dict(existing_meta[key])
            merged.update(meta)
            ind = indent_of(start)
            trailing = "," if lines[end - 1].rstrip().endswith(",") else ""
            new_block = f"{ind}{dumps_value('@' + key)}: {json.dumps(merged, ensure_ascii=False)}{trailing}"
            lines[start - 1:end] = [new_block] + [None] * (end - start)  # type: ignore
            continue
        if not key_lines.get(key):
            continue
        ln = key_lines[key][-1]
        ind = indent_of(ln)
        inserts.setdefault(ln, []).append(f"{ind}{dumps_value('@' + key)}: {json.dumps(meta, ensure_ascii=False)},")

    for ln in sorted(inserts, reverse=True):
        # make sure the anchor line ends with a comma
        if lines[ln - 1] is not None and not lines[ln - 1].rstrip().endswith(","):
            lines[ln - 1] = lines[ln - 1].rstrip() + ","
        lines[ln:ln] = inserts[ln]

    out = "\n".join(l for l in lines if l is not None)
    out = _fix_trailing_comma(out)
    json.loads(out)  # sanity: must still be valid JSON
    return out


def _end_of_member(lines: List[Optional[str]], start: int) -> int:
    depth = 0
    for i in range(start - 1, len(lines)):
        if lines[i] is None:
            continue
        depth += _brace_delta(lines[i])
        if depth <= 0:
            return i + 1
    return start


def _last_member_line(lines: List[Optional[str]]) -> int:
    for i in range(len(lines) - 1, -1, -1):
        if lines[i] is not None and lines[i].strip() and lines[i].strip() != "}":
            return i + 1
    return 1


def _fix_trailing_comma(text: str) -> str:
    # remove a comma that would precede the closing brace of the top object
    return re.sub(r",(\s*)\}\s*$", r"\1}\n", text.rstrip() + "\n")
