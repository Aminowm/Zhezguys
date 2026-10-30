"""Kazakh Cyrillic -> official Latin (2021 alphabet) for a whole ARB, with a length check.

Placeholders, ICU syntax, printf specifiers, markup tags and URLs are left untouched.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from .arb import ArbFile, patch
from .langs import kk_to_latin

PROTECT = re.compile(r"(\{[^{}]*\}|%(?:\d+\$)?[sd]|<[^>]+>|https?://\S+|\\n)")


def translit_text(text: str) -> str:
    parts = PROTECT.split(text)
    return "".join(p if PROTECT.fullmatch(p or "") else kk_to_latin(p) for p in parts)


def translit_arb(tgt: ArbFile, src_msgs: Dict[str, str], max_growth: float = 1.15,
                 ui_max_len: int = 25) -> Tuple[str, List[dict]]:
    """Return (new file text, length warnings). A warning is raised when the Latin form of a
    short UI string is noticeably longer than the Cyrillic one *and* than the source."""
    changes, warns = {}, []
    for k, v in tgt.messages.items():
        lat = translit_text(v)
        if lat != v:
            changes[k] = lat
        src = src_msgs.get(k, "")
        if len(v) <= ui_max_len and len(lat) > max(len(v) * max_growth, len(src) * 1.3) and len(lat) - len(v) >= 2:
            warns.append({"key": k, "cyrillic": v, "latin": lat, "source": src,
                          "evidence": f"{len(lat)} Latin chars vs {len(v)} Cyrillic / {len(src)} source"})
    new = patch(tgt, changes)
    new = re.sub(r'("@@locale"\s*:\s*")kk(")', r"\1kk_Latn\2", new, count=1)
    return new, warns
