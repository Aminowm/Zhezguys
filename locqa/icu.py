"""Tiny ICU MessageFormat parser — enough to validate what ``flutter gen-l10n`` accepts.

Supported:
  simple args      {name}
  typed args       {amount, number}, {date, date, yMd}
  plural           {count, plural, =0{none} one{# item} other{# items}}
  select           {gender, select, male{..} female{..} other{..}}
Also extracts printf-style placeholders (%s, %1$d) used by legacy strings and
inline markup tags (<g>..</g>) that the game renders.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set

# CLDR plural categories per language (cardinal).
PLURAL_CATEGORIES: Dict[str, Set[str]] = {
    "ru": {"one", "few", "many", "other"},
    "uk": {"one", "few", "many", "other"},
    "kk": {"one", "other"},
    "uz": {"one", "other"},
    "en": {"one", "other"},
    "es": {"one", "many", "other"},
    "pt": {"one", "many", "other"},
    "tr": {"one", "other"},
    "de": {"one", "other"},
}
ALL_CATEGORIES = {"zero", "one", "two", "few", "many", "other"}

PRINTF_RE = re.compile(r"%(?:\d+\$)?[-+ 0#]*\d*(?:\.\d+)?[sdif@]")
TAG_RE = re.compile(r"</?([A-Za-z][\w-]*)\s*/?>")


class IcuError(ValueError):
    pass


@dataclass
class Arg:
    name: str
    kind: str = "simple"            # simple | plural | select | number | date | time
    options: Dict[str, "Message"] = field(default_factory=dict)


@dataclass
class Message:
    parts: List[object] = field(default_factory=list)   # str | Arg

    def args(self) -> List[Arg]:
        out = []
        for p in self.parts:
            if isinstance(p, Arg):
                out.append(p)
                for sub in p.options.values():
                    out.extend(sub.args())
        return out

    def placeholder_names(self) -> Set[str]:
        return {a.name for a in self.args()}


_IDENT = re.compile(r"\s*([A-Za-z_][\w]*)\s*")


def parse(text: str, escaping: bool = False) -> Message:
    msg, pos = _parse_message(text, 0, escaping, depth=0)
    if pos != len(text):
        raise IcuError(f"unexpected '}}' at {pos}")
    return msg


def _parse_message(t: str, i: int, esc: bool, depth: int):
    parts: List[object] = []
    buf = []
    n = len(t)
    while i < n:
        c = t[i]
        if esc and c == "'":
            # ICU quoting: '' -> ', '{...}' literal
            if i + 1 < n and t[i + 1] == "'":
                buf.append("'"); i += 2; continue
            j = t.find("'", i + 1)
            if j == -1:
                buf.append(t[i + 1:]); i = n; break
            buf.append(t[i + 1:j]); i = j + 1; continue
        if c == "{":
            if buf:
                parts.append("".join(buf)); buf = []
            arg, i = _parse_arg(t, i + 1, esc, depth)
            parts.append(arg)
            continue
        if c == "}":
            if depth == 0:
                raise IcuError(f"unbalanced '}}' at {i}")
            break
        buf.append(c); i += 1
    if buf:
        parts.append("".join(buf))
    return Message(parts), i


def _parse_arg(t: str, i: int, esc: bool, depth: int):
    m = _IDENT.match(t, i)
    if not m:
        raise IcuError(f"invalid placeholder name at {i}: {t[i:i+15]!r}")
    name = m.group(1)
    i = m.end()
    if i >= len(t):
        raise IcuError(f"unterminated placeholder {{{name}")
    if t[i] == "}":
        return Arg(name), i + 1
    if t[i] != ",":
        raise IcuError(f"unexpected {t[i]!r} in placeholder {{{name}")
    m2 = _IDENT.match(t, i + 1)
    if not m2:
        raise IcuError(f"missing argument type in {{{name}, ...}}")
    kind = m2.group(1)
    i = m2.end()
    if kind in ("plural", "select", "selectordinal"):
        if i >= len(t) or t[i] != ",":
            raise IcuError(f"{kind} for {{{name}}} has no options")
        i += 1
        options: Dict[str, Message] = {}
        while True:
            while i < len(t) and t[i].isspace():
                i += 1
            if i < len(t) and t[i] == "}":
                i += 1
                break
            mo = re.compile(r"(=\d+|[A-Za-z_]\w*)\s*").match(t, i)
            if not mo:
                raise IcuError(f"bad option selector in {{{name}, {kind}}} at {i}")
            sel = mo.group(1)
            i = mo.end()
            if i >= len(t) or t[i] != "{":
                raise IcuError(f"option '{sel}' of {{{name}}} has no '{{...}}' body")
            sub, i = _parse_message(t, i + 1, esc, depth + 1)
            if i >= len(t) or t[i] != "}":
                raise IcuError(f"unterminated option '{sel}' in {{{name}}}")
            i += 1
            if sel in options:
                raise IcuError(f"duplicate option '{sel}' in {{{name}}}")
            options[sel] = sub
        if "other" not in options:
            raise IcuError(f"{kind} {{{name}}} is missing the required 'other' form")
        return Arg(name, "plural" if kind != "select" else "select", options), i
    # {x, number} / {x, number, compact} / {d, date, yMd}
    j = t.find("}", i)
    if j == -1:
        raise IcuError(f"unterminated typed placeholder {{{name}, {kind}")
    return Arg(name, kind), j + 1


def placeholders(text: str) -> Set[str]:
    """ICU placeholder names; falls back to a regex if the text does not parse."""
    try:
        return parse(text).placeholder_names()
    except IcuError:
        return set(re.findall(r"\{\s*([A-Za-z_]\w*)", text))


def printf_placeholders(text: str) -> List[str]:
    return sorted(PRINTF_RE.findall(text))


def tags(text: str) -> List[str]:
    return sorted(m.group(0).replace(" ", "") for m in TAG_RE.finditer(text))


def plural_args(text: str) -> List[Arg]:
    try:
        return [a for a in parse(text).args() if a.kind == "plural"]
    except IcuError:
        return []
