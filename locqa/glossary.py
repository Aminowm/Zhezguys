"""Glossary loader.

CSV columns (header required; only the first four are mandatory):

  term_ru, term_kk, term_en, comment, ru_pattern, kk_allowed, kk_forbidden, kk_variants, replacements

* ``ru_pattern``   regex matched against the SOURCE (default: stem of term_ru)
* ``kk_allowed``   regex; if the source matches and the target does not -> low-confidence violation
* ``kk_forbidden`` regex; a hit in the target -> glossary_violation (medium, 0.85)
* ``kk_variants``  regex of tolerated-but-non-preferred variants -> inconsistent_term (low)
* ``replacements`` ``bad=>good|bad2=>good2`` used to build an automatic suggestion
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Term:
    ru: str
    kk: str
    en: str = ""
    comment: str = ""
    src_re: Optional[re.Pattern] = None
    allowed_re: Optional[re.Pattern] = None
    forbidden_re: Optional[re.Pattern] = None
    variant_re: Optional[re.Pattern] = None
    replacements: Dict[str, str] = field(default_factory=dict)


@dataclass
class Glossary:
    terms: List[Term]

    @classmethod
    def load(cls, path: str) -> "Glossary":
        terms = []
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
                ru, kk = row.get("term_ru", ""), row.get("term_kk", "")
                if not ru:
                    continue
                t = Term(ru=ru, kk=kk, en=row.get("term_en", ""), comment=row.get("comment", ""))
                pat = row.get("ru_pattern") or _stem_pattern(ru)
                t.src_re = re.compile(pat, re.I)
                if row.get("kk_allowed"):
                    t.allowed_re = re.compile(row["kk_allowed"], re.I)
                if row.get("kk_forbidden"):
                    t.forbidden_re = re.compile(row["kk_forbidden"], re.I)
                if row.get("kk_variants"):
                    t.variant_re = re.compile(row["kk_variants"], re.I)
                for pair in filter(None, row.get("replacements", "").split("|")):
                    if "=>" in pair:
                        a, b = pair.split("=>", 1)
                        t.replacements[a.strip().lower()] = b.strip()
                terms.append(t)
        return cls(terms)


def _stem_pattern(term: str) -> str:
    words = term.split()
    parts = [re.escape(w[: max(4, len(w) - 2)]) if len(w) > 4 else re.escape(w) for w in words]
    return r"\b" + r"\w*\s+".join(parts) + r"\w*"
