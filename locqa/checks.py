"""All QA checks. Each check is a function ``(ctx) -> list[Finding]``.

Design rules (they are what keeps the report precise):
  * every finding carries human-readable *evidence* with the numbers/rule that fired;
  * confidence is calibrated per check and adjusted by cross-signals
    (translation memory, reference locales, key-name hints);
  * anything that is merely "suspicious" gets low confidence and is filtered by
    ``--min-confidence`` instead of polluting the report.
"""
from __future__ import annotations

import collections
import re
import statistics
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from . import icu
from .arb import ArbFile
from .glossary import Glossary
from .langs import (CYR_WORD_RE, EN_MARKERS, KK_AFFIX_CASE, CASE_NAMES, LATIN_HOMOGLYPHS, PT_ES_MARKERS,
                    RU_ENDINGS, RU_FUNCTION_WORDS, UZ_MARKERS, Profile, kk_rephrase_affix, profile_for,
                    script_counts)

ISSUE_TYPES = [
    "untranslated", "same_as_source", "placeholder_mismatch", "plural_mismatch", "glossary_violation",
    "length_overflow", "machine_translation_suspect", "inconsistent_term", "suffix_harmony",
    "orthography", "wrong_language",
]
SEVERITIES = ("high", "medium", "low")


@dataclass
class Finding:
    key: str
    issue_type: str
    severity: str
    source: str
    target: str
    evidence: str
    suggestion: Optional[str]
    confidence: float
    reviewed_by_human: bool = False
    line: int = 0
    section: str = ""
    detector: str = ""          # which rule produced it (for explainability / ablation)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidence"] = round(float(self.confidence), 2)
        return d


@dataclass
class Context:
    src: ArbFile
    tgt: ArbFile
    profile: Profile
    glossary: Optional[Glossary] = None
    refs: Dict[str, Dict[str, str]] = field(default_factory=dict)   # locale -> messages
    src_msgs: Dict[str, str] = field(default_factory=dict)
    tgt_msgs: Dict[str, str] = field(default_factory=dict)
    sections: Dict[str, str] = field(default_factory=dict)
    tm: Dict[str, collections.Counter] = field(default_factory=dict)  # normalised source -> Counter(target)
    vocab: collections.Counter = field(default_factory=collections.Counter)
    median_ratio: float = 1.0
    warnings: List[dict] = field(default_factory=list)

    @classmethod
    def build(cls, src: ArbFile, tgt: ArbFile, glossary=None, refs=None, locale=None) -> "Context":
        ctx = cls(src=src, tgt=tgt, profile=profile_for(locale or tgt.locale), glossary=glossary,
                  refs=refs or {})
        ctx.src_msgs = src.messages
        ctx.tgt_msgs = tgt.messages
        ctx.sections = tgt.section_of() or src.section_of()
        for k, s in ctx.src_msgs.items():
            t = ctx.tgt_msgs.get(k)
            if t:
                ctx.tm.setdefault(_norm(s), collections.Counter())[t] += 1
        for t in ctx.tgt_msgs.values():
            for w in CYR_WORD_RE.findall(t):
                ctx.vocab[w.lower()] += 1
        ratios = [len(ctx.tgt_msgs[k]) / len(s) for k, s in ctx.src_msgs.items()
                  if len(s) >= 15 and ctx.tgt_msgs.get(k)]
        ctx.median_ratio = statistics.median(ratios) if ratios else 1.0
        return ctx

    def finding(self, key, issue_type, severity, evidence, suggestion, confidence, detector) -> Finding:
        return Finding(key=key, issue_type=issue_type, severity=severity,
                       source=self.src_msgs.get(key, ""), target=self.tgt_msgs.get(key, ""),
                       evidence=evidence, suggestion=suggestion, confidence=max(0.0, min(1.0, confidence)),
                       line=self.tgt.line_of(key), section=self.sections.get(key, ""), detector=detector)

    def tm_suggestion(self, key: str, exclude: Iterable[str] = ()) -> Optional[str]:
        """Most frequent target used elsewhere for the same source text."""
        s = self.src_msgs.get(key, "")
        c = self.tm.get(_norm(s))
        if not c:
            return None
        bad = set(exclude) | {self.tgt_msgs.get(key, ""), s}
        for t, _n in c.most_common():
            if t not in bad and t.strip():
                return _match_case(t, s)
        return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _norm_loose(s: str) -> str:
    return re.sub(r"[\s\W_]+", " ", s.lower()).strip()


def _match_case(t: str, s: str) -> str:
    letters = [c for c in s if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return t.upper()
    if s[:1].isupper() and t[:1].islower():
        return t[:1].upper() + t[1:]
    if s[:1].islower() and t[:1].isupper() and not t.isupper():
        return t[:1].lower() + t[1:]
    return t


def _words(s: str) -> List[str]:
    s = re.sub(r"\{[^{}]*\}|<[^>]*>|%\d*\$?[sd]", " ", s)
    return [w.lower() for w in CYR_WORD_RE.findall(s)]


def _has_letters(s: str) -> bool:
    return bool(re.search(r"[^\W\d_]", s))


UI_KEY_RE = re.compile(r"(button|btn|tab|title|header|label|caption|menu|hint|chip|badge|cancel|ok|close|buy|"
                       r"select|choose|confirm|next|back|start|play|_\d+$)", re.I)


# ==========================================================================
# 1. untranslated
# ==========================================================================

def check_untranslated(ctx: Context) -> List[Finding]:
    out = []
    for k, s in ctx.src_msgs.items():
        if not s.strip():
            continue
        if k not in ctx.tgt_msgs:
            sugg = ctx.tm_suggestion(k)
            out.append(ctx.finding(
                k, "untranslated", "high",
                f"key exists in source (line {ctx.src.line_of(k)}) but is missing in target; the app falls back to "
                f"the source language" + (" — an existing translation of the same text was found in TM" if sugg else ""),
                sugg, 0.97, "missing_key"))
        elif not ctx.tgt_msgs[k].strip():
            words = _words(s)
            # empty translation of a lone preposition/particle can be intentional
            conf = 0.45 if (len(words) <= 1 and len(s.strip()) <= 6) else 0.8
            if any(k in r and not r[k].strip() for r in ctx.refs.values()):
                conf = 0.35  # another locale also leaves it empty: word-order prefix/suffix split
            out.append(ctx.finding(
                k, "untranslated", "medium" if conf > 0.5 else "low",
                f"target is empty while source is {s!r}" +
                (" (short particle — may be intentionally dropped by Kazakh word order, verify in context)" if conf < 0.5 else ""),
                ctx.tm_suggestion(k), conf, "empty_value"))
    for k in ctx.tgt_msgs:
        if k not in ctx.src_msgs:
            ctx.warnings.append({"key": k, "type": "extra_key",
                                 "message": "key not present in source (dead string or renamed key)"})
    for k, occ in ctx.tgt.duplicates().items():
        vals = [o.value for o in occ]
        if len({str(v) for v in vals}) > 1:
            ctx.warnings.append({"key": k, "type": "duplicate_key",
                                 "message": f"defined {len(occ)} times with different values on lines "
                                            f"{[o.line for o in occ]}; runtime uses the last one",
                                 "values": vals})
    return out


# ==========================================================================
# 2. same_as_source
# ==========================================================================

RU_INFINITIVE = re.compile(r"(ать|ять|ить|еть|уть|ться|чь)$")
RU_ADJ_PL = re.compile(r"(ые|ие|ая|ое|ий|ый|ой|ых|их)$")


def check_same_as_source(ctx: Context) -> List[Finding]:
    out = []
    loan = ctx.profile.loanwords
    for k, s in ctx.src_msgs.items():
        t = ctx.tgt_msgs.get(k)
        if not t or t != s:
            continue
        words = _words(s)
        if not words:
            continue                                        # numbers, latin brands, symbols
        cyr, lat = script_counts(s)
        if cyr == 0:
            continue
        content = [w for w in words if w not in loan]
        if not content:
            continue                                        # only loanwords: Гараж, Бонус ...
        fw = [w for w in words if w in RU_FUNCTION_WORDS]
        tm_alt = ctx.tm_suggestion(k)
        if len(words) >= 3:
            conf, why = 0.9, f"{len(words)}-word Russian phrase copied verbatim"
        elif any(RU_INFINITIVE.search(w) for w in content):
            conf, why = 0.88, "Russian verb form copied verbatim"
        elif len(words) == 2:
            conf, why = 0.55, "two-word Russian phrase copied verbatim"
            if all(w[:1].isupper() for w in re.findall(r"[^\W\d_]+", s)) and not s.isupper():
                conf, why = 0.4, "looks like a proper name (all words capitalised) — likely legitimate"
        elif RU_ADJ_PL.search(content[0]) and len(content[0]) > 4:
            conf, why = 0.7, "Russian inflected word copied verbatim"
        else:
            conf, why = 0.45, "single word identical to Russian (may be an accepted loanword)"
        if fw:
            conf += 0.05; why += f"; contains Russian function words {fw}"
        if tm_alt:
            conf = max(conf, 0.85); why += f"; the same source is translated elsewhere as {tm_alt!r}"
        for loc, ref in ctx.refs.items():
            r = ref.get(k)
            if r and r != s and conf < 0.6:
                conf -= 0.05  # reference locales translated it — mild signal only
        if ctx.profile.special_letters and re.search(f"[{ctx.profile.special_letters}]", t):
            continue
        sev = "high" if conf >= 0.8 else "medium" if conf >= 0.6 else "low"
        out.append(ctx.finding(k, "same_as_source", sev, why, tm_alt, conf, "identical_to_source"))
    return out


# ==========================================================================
# 3. placeholder_mismatch  &  4. plural_mismatch
# ==========================================================================

def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _repair_placeholders(s: str, t: str) -> Optional[str]:
    src_ph = icu.placeholders(s)
    tgt_ph = icu.placeholders(t)
    fixed = t
    # wrong names: {platformColo} -> {platformColor}
    for bad in sorted(tgt_ph - src_ph):
        cands = sorted(src_ph - tgt_ph, key=lambda g: _edit_distance(bad, g))
        if cands and _edit_distance(bad, cands[0]) <= max(2, len(cands[0]) // 3):
            fixed = re.sub(r"\{\s*" + re.escape(bad) + r"\s*\}", "{" + cands[0] + "}", fixed)
    # stripped braces: "time" -> "{time}"
    for name in sorted(src_ph - icu.placeholders(fixed)):
        pat = re.compile(r"(?<![\w{])" + re.escape(name) + r"(?![\w}])")
        if pat.search(fixed):
            fixed = pat.sub("{" + name + "}", fixed, count=1)
    if icu.placeholders(fixed) == src_ph and fixed != t:
        return fixed
    return None


def check_placeholders(ctx: Context) -> List[Finding]:
    out = []
    for k, s in ctx.src_msgs.items():
        t = ctx.tgt_msgs.get(k)
        if not t:
            continue
        problems = []
        try:
            sm = icu.parse(s)
            s_ok = True
        except icu.IcuError:
            s_ok = False
        try:
            tm = icu.parse(t)
            t_err = None
        except icu.IcuError as e:
            tm, t_err = None, str(e)
        sp, tp = icu.placeholders(s), icu.placeholders(t)
        if t_err and s_ok:
            problems.append(f"target is not valid ICU ({t_err}); flutter gen-l10n will fail")
        missing, extra = sorted(sp - tp), sorted(tp - sp)
        if missing:
            problems.append(f"missing placeholder(s) {['{'+m+'}' for m in missing]}")
        if extra:
            problems.append(f"unknown placeholder(s) {['{'+m+'}' for m in extra]} not in source"
                            " (gen-l10n rejects them)")
        pf_s, pf_t = icu.printf_placeholders(s), icu.printf_placeholders(t)
        if pf_s != pf_t:
            problems.append(f"printf placeholders differ: source {pf_s} vs target {pf_t}")
        tg_s, tg_t = icu.tags(s), icu.tags(t)
        if tg_s != tg_t:
            problems.append(f"markup tags differ: source {tg_s} vs target {tg_t}")
        if problems:
            sugg = _repair_placeholders(s, t)
            if sugg is None and missing and not extra:
                # bare word lost entirely -> prepend placeholder as safest repair hint
                sugg = None
            if any("plural" in p for p in problems) or re.search(r"\{\s*\w+\s*,\s*(plural|select)", s + t):
                pass  # reported by plural check
            out.append(ctx.finding(k, "placeholder_mismatch", "high", "; ".join(problems), sugg,
                                   0.97 if (missing or extra or t_err) else 0.85, "placeholder_diff"))
    return out


def check_plurals(ctx: Context) -> List[Finding]:
    out = []
    cats = ctx.profile.plural_categories
    for k, s in ctx.src_msgs.items():
        t = ctx.tgt_msgs.get(k)
        if not t:
            continue
        sp = {a.name: a for a in icu.plural_args(s)}
        has_plural_syntax = bool(re.search(r"\{\s*\w+\s*,\s*plural", t))
        try:
            targs = {a.name: a for a in icu.parse(t).args() if a.kind == "plural"}
        except icu.IcuError as e:
            if has_plural_syntax or sp:
                out.append(ctx.finding(k, "plural_mismatch", "high", f"broken ICU plural: {e}", None, 0.95,
                                       "plural_parse"))
            continue
        problems = []
        for name, a in sp.items():
            if name not in targs:
                problems.append(f"source pluralises {{{name}}} but target has no plural for it")
        for name, a in targs.items():
            sel = set(a.options)
            unknown = {x for x in sel if not x.startswith("=") and x not in cats}
            if unknown:
                problems.append(f"plural {{{name}}} uses categories {sorted(unknown)} that do not exist in "
                                f"'{ctx.profile.code}' (valid: {sorted(cats)})")
            missing = {x for x in cats if x not in sel and x != "one"}
            if missing:
                problems.append(f"plural {{{name}}} lacks forms {sorted(missing)}")
            # placeholders used inside forms must exist in source
            for form, sub in a.options.items():
                bad = sub.placeholder_names() - icu.placeholders(s)
                if bad:
                    problems.append(f"form '{form}' references unknown {sorted(bad)}")
        if problems:
            out.append(ctx.finding(k, "plural_mismatch", "high", "; ".join(problems), None, 0.9, "plural_forms"))
    return out


# ==========================================================================
# 5. glossary_violation  &  8. inconsistent_term
# ==========================================================================

def check_glossary(ctx: Context) -> List[Finding]:
    if not ctx.glossary:
        return []
    out = []
    for k, s in ctx.src_msgs.items():
        t = ctx.tgt_msgs.get(k)
        if not t:
            continue
        for term in ctx.glossary.terms:
            if not term.src_re or not term.src_re.search(s):
                continue
            if term.forbidden_re and term.forbidden_re.search(t):
                m = term.forbidden_re.search(t).group(0)
                out.append(ctx.finding(
                    k, "glossary_violation", "medium",
                    f"source term «{term.ru}» rendered as «{m}»; glossary requires «{term.kk}» ({term.comment})",
                    _fix_case(_glossary_fix(t, term, m), s), 0.85, f"glossary:{term.ru}"))
            elif term.allowed_re and not term.allowed_re.search(t) and not _same_as_source(s, t):
                out.append(ctx.finding(
                    k, "glossary_violation", "low",
                    f"source contains «{term.ru}» but none of the approved renderings «{term.kk}» appear in target",
                    None, 0.45, f"glossary_missing:{term.ru}"))
    return out


def _same_as_source(s, t):
    return s.strip() == t.strip()


def _fix_case(fix: Optional[str], s: str) -> Optional[str]:
    return _match_case(fix, s) if fix else fix


def _glossary_fix(t: str, term, bad: str) -> Optional[str]:
    if term.replacements and bad.lower() in term.replacements:
        rep = term.replacements[bad.lower()]
        if bad.isupper() and len(bad) > 1:
            rep = rep.upper()
        elif bad[:1].isupper():
            rep = rep[:1].upper() + rep[1:]
        return t.replace(bad, rep)
    return None


def check_inconsistent(ctx: Context) -> List[Finding]:
    """Two signals:
    (a) TM: identical source strings translated differently — the minority variant is
        flagged only when a clear majority exists (>=3 uses and >=70% share);
    (b) term alignment: a source stem that maps to one target stem in >=90% of strings
        (e.g. «игрок»→«ойынш») but not in this one — the outlier is a candidate.
    """
    out = []
    groups: Dict[str, List[str]] = collections.defaultdict(list)
    for k, s in ctx.src_msgs.items():
        if ctx.tgt_msgs.get(k) and _has_letters(s) and len(s) <= 60:
            groups[_norm(s)].append(k)
    for ns, keys in groups.items():
        if len(keys) < 3:
            continue
        c = collections.Counter(_norm_loose(ctx.tgt_msgs[k]) for k in keys)
        (top, n1), *rest = c.most_common()
        share = n1 / len(keys)
        if n1 < 2 or (rest and rest[0][1] == n1):
            continue
        conf = 0.65 if (n1 >= 3 and share >= 0.7) else 0.4
        canonical = next(ctx.tgt_msgs[k] for k in keys if _norm_loose(ctx.tgt_msgs[k]) == top)
        for k in keys:
            if _norm_loose(ctx.tgt_msgs[k]) != top:
                others = [x for x in keys if _norm_loose(ctx.tgt_msgs[x]) == top][:3]
                out.append(ctx.finding(
                    k, "inconsistent_term", "medium" if conf > 0.5 else "low",
                    f"source {ctx.src_msgs[k]!r} is translated as {canonical!r} in {n1} of {len(keys)} keys "
                    f"(e.g. {others}) but as {ctx.tgt_msgs[k]!r} here",
                    _match_case(canonical, ctx.src_msgs[k]), conf, "tm_majority"))
    out.extend(_term_alignment_outliers(ctx))
    # glossary preferred variants
    if ctx.glossary:
        for k, s in ctx.src_msgs.items():
            t = ctx.tgt_msgs.get(k)
            if not t:
                continue
            for term in ctx.glossary.terms:
                if term.src_re and term.src_re.search(s) and term.variant_re:
                    m = term.variant_re.search(t)
                    if m:
                        out.append(ctx.finding(
                            k, "inconsistent_term", "low",
                            f"«{term.ru}» rendered as non-preferred variant «{m.group(0)}»; the file mostly uses "
                            f"«{term.kk}»", _glossary_fix(t, term, m.group(0)), 0.45, f"variant:{term.ru}"))
    return out


_STEM_W = re.compile(r"[^\W\d_]{4,}")


def _stems(s: str, n: int) -> Set[str]:
    s = re.sub(r"\{[^{}]*\}|<[^>]*>", " ", s)
    return {w.lower()[:n] for w in _STEM_W.findall(s)}


def _term_alignment_outliers(ctx: Context, min_support: int = 10) -> List[Finding]:
    keys = [k for k, s in ctx.src_msgs.items() if ctx.tgt_msgs.get(k) and ctx.tgt_msgs[k] != s]
    rs = {k: _stems(ctx.src_msgs[k], 5) for k in keys}
    ts = {k: _stems(ctx.tgt_msgs[k], 4) for k in keys}
    R, T, C = collections.Counter(), collections.Counter(), collections.Counter()
    for k in keys:
        R.update(rs[k]); T.update(ts[k])
        for a in rs[k]:
            for b in ts[k]:
                C[a, b] += 1
    best: Dict[str, Tuple[float, str]] = {}
    for (a, b), c in C.items():
        if R[a] >= min_support:
            p, d = c / R[a], 2 * c / (R[a] + T[b])
            if p >= 0.9 and d >= 0.8 and p > best.get(a, (0, ""))[0]:
                best[a] = (p, b)
    out = []
    for a, (p, b) in best.items():
        miss = [k for k in keys if a in rs[k] and b not in ts[k]]
        if len(miss) > 4:
            continue
        for k in miss:
            out.append(ctx.finding(
                k, "inconsistent_term", "low",
                f"source stem «{a}…» is rendered with «{b}…» in {p:.0%} of {R[a]} strings, but not here",
                None, 0.42, "term_alignment"))
    return out


# ==========================================================================
# 6. length_overflow
# ==========================================================================

DEFAULT_PADDING = [
    r"click here", r"нажмите здесь", r"нажмите для продолжения",
]


def _learn_padding(ctx: Context, min_words: int = 3, min_support: int = 2) -> List[str]:
    """Filler phrases appended to short labels repeat across the file. Learn them: trailing
    n-grams (>=3 words) of strongly expanded short strings that occur in >=2 such strings."""
    grams = collections.Counter()
    for k, s in ctx.src_msgs.items():
        t = ctx.tgt_msgs.get(k)
        if not t or len(s.strip()) > 25 or len(t) < 2.5 * max(len(s.strip()), 1):
            continue
        w = t.split()
        for n in range(min_words, min(6, len(w)) + 1):
            if len(w) - n >= 1:          # something must remain before the filler
                grams[" ".join(w[-n:]).lower()] += 1
    learned = [g for g, c in grams.items() if c >= min_support]
    # keep the longest variant of nested phrases
    learned.sort(key=len, reverse=True)
    final: List[str] = []
    for g in learned:
        if not any(g in f for f in final):
            final.append(g)
    return final


def check_length(ctx: Context, padding: Optional[List[str]] = None) -> List[Finding]:
    out = []
    pads = [re.compile(re.escape(p) if not any(c in p for c in "\\$^") else p, re.I)
            for p in (padding or DEFAULT_PADDING) + _learn_padding(ctx)]
    for k, s in ctx.src_msgs.items():
        t = ctx.tgt_msgs.get(k)
        if not t or not _has_letters(s):
            continue
        ls, lt = len(s.strip()), len(t.strip())
        short_ui = ls <= 25 and len(s.split()) <= 3 or (UI_KEY_RE.search(k) and ls <= 30)
        if not short_ui:
            continue
        pad = next((p for p in pads if p.search(t) and not p.search(s)), None)
        ratio = lt / max(ls, 1)
        ref_lens = {loc: len(r.get(k, "")) for loc, r in ctx.refs.items() if r.get(k)}
        if pad:
            m = pad.search(t)
            stripped = (t[:m.start()] + t[m.end():]).strip(" :,.-")
            out.append(ctx.finding(
                k, "length_overflow", "high",
                f"{lt} chars vs {ls} in source (x{ratio:.1f}); short UI element padded with filler phrase "
                f"«{m.group(0)}»", stripped or None, 0.95, "padding_phrase"))
            continue
        limit = max(ls * 2.0 * ctx.median_ratio, ls + 10)
        if lt <= limit:
            continue
        conf = min(0.8, 0.45 + 0.08 * ratio)
        ev = f"{lt} chars vs {ls} in source (x{ratio:.1f}, file median x{ctx.median_ratio:.2f})"
        if ref_lens:
            mx = max(ref_lens.values())
            ev += "; reference lengths " + ", ".join(f"{l}={n}" for l, n in ref_lens.items())
            if lt <= mx * 1.4:
                conf -= 0.2       # other locales are long too: natural expansion
            else:
                conf += 0.05
        if UI_KEY_RE.search(k):
            ev += "; key looks like a button/tab/title"
            conf += 0.05
        sev = "medium" if conf >= 0.6 else "low"
        out.append(ctx.finding(k, "length_overflow", sev, ev, None, conf, "length_ratio"))
    return out


# ==========================================================================
# 7. machine_translation_suspect
# ==========================================================================

def check_mt(ctx: Context) -> List[Finding]:
    if ctx.profile.script != "cyrl" or not ctx.profile.special_letters:
        return []
    out = []
    special = set(ctx.profile.special_letters)
    for k, s in ctx.src_msgs.items():
        t = ctx.tgt_msgs.get(k)
        if not t or t == s:
            continue
        words = _words(t)
        if len(words) < 2:
            continue
        native = [w for w in words if set(w) & special]
        ru_fw = [w for w in words if w in RU_FUNCTION_WORDS and w not in ("не",)]
        ru_infl = [w for w in words if not (set(w) & special) and len(w) > 4 and w.endswith(RU_ENDINGS)
                   and w not in ctx.profile.loanwords and w in _words(s)]
        if native and (len(ru_fw) >= 2 or (ru_fw and ru_infl)):
            out.append(ctx.finding(
                k, "machine_translation_suspect", "high",
                f"Kazakh sentence mixed with Russian grammar: function words {ru_fw}"
                + (f", Russian-inflected words {ru_infl}" if ru_infl else ""), None, 0.8, "ru_grammar_mix"))
        elif native and len(ru_infl) >= 2:
            out.append(ctx.finding(
                k, "machine_translation_suspect", "medium",
                f"Russian word forms left inside Kazakh text: {ru_infl}", None, 0.6, "ru_inflection"))
        # literal calques frequently produced by MT engines
        for pat, why, fix in MT_CALQUES:
            m = re.search(pat, t, re.I)
            if m and not re.search(pat, s, re.I):
                out.append(ctx.finding(k, "machine_translation_suspect", "medium",
                                       f"calque «{m.group(0)}»: {why}", fix(t, m) if fix else None, 0.7,
                                       "calque_pattern"))
                break
    return out


MT_CALQUES: List[Tuple[str, str, Optional[Callable]]] = [
    (r"\bтерілер\w*", "«скины» translated as animal hides; game term is «скиндер»",
     lambda t, m: t.replace(m.group(0), m.group(0).replace("терілер", "скиндер"))),
    (r"\bжаңа жылдың жаңа жыл", "duplicated phrase typical for MT", None),
]


# ==========================================================================
# 9. suffix_harmony
# ==========================================================================

AFFIX_AFTER_PH = re.compile(r"(\{\s*\w+\s*\}|%(?:\d+\$)?[sd])([-‑–’'`]?)([А-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі]+)")


def check_suffix(ctx: Context) -> List[Finding]:
    if ctx.profile.code != "kk":
        return []
    out = []
    for k, s in ctx.src_msgs.items():
        t = ctx.tgt_msgs.get(k)
        if not t:
            continue
        hits, sugg = [], t
        for m in AFFIX_AFTER_PH.finditer(t):
            ph, sep, aff = m.groups()
            case = KK_AFFIX_CASE.get(aff.lower())
            if not case:
                continue  # e.g. "{place}-орын" (ordinal + noun) is fine
            hits.append((m.group(0), ph, aff, case))
            rep = kk_rephrase_affix(ph, aff)
            if rep:
                sugg = sugg.replace(m.group(0), rep, 1)
        if hits:
            desc = "; ".join(f"«{h[0]}» hard-codes the {CASE_NAMES[h[3]]} affix «-{h[2]}» — wrong when the "
                             f"substituted value ends in a {'front' if h[2][-1:] in 'ғқ' or h[2] in ('да','дан','ның','ны','ды','ты','тан','та','на','нан','дың','тың') else 'back'}-vowel/"
                             f"different consonant" for h in hits)
            out.append(ctx.finding(
                k, "suffix_harmony", "medium",
                desc + ". Rephrase with a head noun that carries the affix, or mark for code-side affix selection "
                       "(see docs/dart/kk_affix.dart)",
                sugg if sugg != t else None, 0.9, "affix_after_placeholder"))
    return out


# ==========================================================================
# 10. orthography
# ==========================================================================

def check_orthography(ctx: Context) -> List[Finding]:
    out = []
    prof = ctx.profile
    for k, t in ctx.tgt_msgs.items():
        if k not in ctx.src_msgs or not t:
            continue
        clean = re.sub(r"\{[^{}]*\}|<[^>]*>|https?://\S+", " ", t)
        # (a) latin homoglyphs inside cyrillic words (or vice versa)
        for w in re.findall(r"[\wʻʼ‘’]+", clean):
            if re.search(r"[A-Za-z]", w) and re.search(r"[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]", w):
                fixed = "".join(LATIN_HOMOGLYPHS.get(c, c) for c in w) if prof.script == "cyrl" else w
                out.append(ctx.finding(k, "orthography", "high",
                                       f"mixed Latin/Cyrillic letters in «{w}» (look-alike characters)",
                                       t.replace(w, fixed) if fixed != w else None, 0.93, "mixed_script"))
    if prof.lookalikes:
        out.extend(_lookalike_typos(ctx))
    if prof.code == "kk":
        out.extend(_kk_suffix_letters(ctx))
    out.extend(_mixed_case(ctx))
    if prof.code == "uz":
        out.extend(_uz_apostrophes(ctx))
    # dedupe per key (keep highest confidence, merge evidence)
    merged: Dict[str, Finding] = {}
    for f in out:
        if f.key in merged:
            g = merged[f.key]
            g.evidence += "; " + f.evidence
            if f.suggestion:
                base = g.suggestion or g.target
                # re-apply this finding's replacement on top of the previous suggestion
                g.suggestion = _apply_word_fix(base, f.target, f.suggestion)
            g.confidence = max(g.confidence, f.confidence)
        else:
            merged[f.key] = f
    return list(merged.values())


def _apply_word_fix(base: str, orig: str, fixed: str) -> str:
    import difflib
    sm = difflib.SequenceMatcher(None, orig, fixed)
    res = base
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            # widen to whole word for a safe replace
            a = orig[max(0, i1 - 6):i2 + 6]
            b = orig[max(0, i1 - 6):i1] + fixed[j1:j2] + orig[i2:i2 + 6]
            if a in res:
                res = res.replace(a, b, 1)
    return res


def _lookalike_typos(ctx: Context) -> List[Finding]:
    prof = ctx.profile
    rev = {}
    for kz, ru in prof.lookalikes.items():
        rev.setdefault(kz.lower(), ru.lower())
    norm_tab = str.maketrans({k: v for k, v in rev.items()})
    groups: Dict[str, List[str]] = collections.defaultdict(list)
    for w in ctx.vocab:
        groups[w.translate(norm_tab)].append(w)
    special = set(prof.special_letters.lower())
    best: Dict[str, str] = {}
    for nk, ws in groups.items():
        if len(ws) < 2:
            continue
        ws = sorted(ws, key=lambda w: (-ctx.vocab[w], -sum(c in special for c in w)))
        for w in ws:
            cand = max(ws, key=lambda x: (sum(c in special for c in x), ctx.vocab[x]))
            if cand == w:
                continue
            if sum(c in special for c in w) >= sum(c in special for c in cand):
                continue
            if ctx.vocab[w] > 3 or ctx.vocab[cand] < 2 or ctx.vocab[cand] < ctx.vocab[w]:
                continue
            if _only_possessive_n(w, cand) or w in KK_VALID_MINIMAL_PAIRS:
                continue
            best[w] = cand
    out = []
    for k, t in ctx.tgt_msgs.items():
        if k not in ctx.src_msgs:
            continue
        for w in set(CYR_WORD_RE.findall(t)):
            lw = w.lower()
            if lw in best and len(lw) > 2:
                cand = best[lw]
                rep = cand.upper() if w.isupper() else (cand[:1].upper() + cand[1:] if w[:1].isupper() else cand)
                diff = [f"{a}→{b}" for a, b in zip(lw, cand) if a != b]
                out.append(ctx.finding(
                    k, "orthography", "high",
                    f"«{w}» uses Russian look-alike letters ({', '.join(diff)}); the correct form «{rep}» occurs "
                    f"{ctx.vocab[cand]}× in the file", re.sub(rf"(?<!\w){re.escape(w)}(?!\w)", rep, t), 0.92,
                    "lookalike_letter"))
    return out


# legitimate words that differ only by a special letter
KK_VALID_MINIMAL_PAIRS = {"ок", "торт", "к", "с", "м", "н", "өзіне", "кен", "сан", "тон", "кол", "ас", "ал", "он",
                          "бас", "от", "қорын", "үлесін", "сыйлығын"}


def _only_possessive_n(a: str, b: str) -> bool:
    d = [(x, y) for x, y in zip(a, b) if x != y]
    return len(a) == len(b) and len(d) == 1 and d[0] == ("н", "ң") and a.endswith("н")


KK_RU_I_SUFFIX = re.compile(r"(?<=[әөүіӘӨҮІ])([^\W\d_]*?)(ңиз(дер|ді|ге)?|дик|тик|лик|ңизге)$", re.I)


def _kk_suffix_letters(ctx: Context) -> List[Finding]:
    """Russian «и» inside a Kazakh front-vowel suffix (-ңіз, -дік, -лік): тексеріңиз, Мезгілдик;
    and «-ьди» after a soft-sign loanword (Автомобильди → Автомобильді)."""
    out = []
    for k, t in ctx.tgt_msgs.items():
        if k not in ctx.src_msgs:
            continue
        fixed, bad = t, []
        for w in set(CYR_WORD_RE.findall(t)):
            m = KK_RU_I_SUFFIX.search(w)
            rep = None
            if m and re.search(r"[әөүі]", w.lower()):
                suf = m.group(2)
                rep = w[:m.start(2)] + suf.replace("и", "і").replace("И", "І")
            elif re.search(r"ь(ди|ни|ти)$", w.lower()):
                rep = w[:-1] + ("і" if w[-1] == "и" else "І")
            if rep and rep != w:
                bad.append(f"«{w}»→«{rep}»")
                fixed = re.sub(rf"(?<!\w){re.escape(w)}(?!\w)", rep, fixed)
        if bad:
            out.append(ctx.finding(k, "orthography", "medium",
                                   "Russian «и» in a Kazakh front-vowel suffix (vowel harmony requires «і»): "
                                   + ", ".join(bad), fixed, 0.9, "suffix_letter"))
    return out


def _mixed_case(ctx: Context) -> List[Finding]:
    out = []
    for k, t in ctx.tgt_msgs.items():
        if k not in ctx.src_msgs:
            continue
        for w in set(re.findall(r"[^\W\d_]{3,}", t)):
            if re.fullmatch(r"[^\W\d_a-zа-яёәғқңөұүһі]{2,}[a-zа-яёәғқңөұүһі]", w) and w[:-1].isupper():
                out.append(ctx.finding(k, "orthography", "low", f"broken capitalisation in «{w}»",
                                       t.replace(w, w.upper()), 0.85, "mixed_case"))
    return out


def _uz_apostrophes(ctx: Context) -> List[Finding]:
    """Uzbek: o‘/g‘ must use U+02BB (or the file's dominant variant consistently)."""
    variants = collections.Counter()
    for t in ctx.tgt_msgs.values():
        variants.update(re.findall(r"[oOgG]([‘’ʻʼ'`])", t))
    if not variants:
        return []
    dominant = variants.most_common(1)[0][0]
    out = []
    for k, t in ctx.tgt_msgs.items():
        if k not in ctx.src_msgs:
            continue
        hits = re.findall(r"[oOgG]([‘’ʻʼ'`])(?!(?:ni|ga|da|dan|ning|lar|dagi)\b)", t)
        bad = {m for m in hits if m != dominant}
        if bad:
            fixed = re.sub(r"([oOgG])[‘’ʻʼ'`]", lambda m: m.group(1) + dominant, t)
            out.append(ctx.finding(k, "orthography", "low",
                                   f"o‘/g‘ written with {sorted(bad)} instead of the file's standard «{dominant}» "
                                   f"(U+{ord(dominant):04X})", fixed, 0.6, "uz_apostrophe"))
    return out


# ==========================================================================
# 11. wrong_language
# ==========================================================================

def check_wrong_language(ctx: Context) -> List[Finding]:
    out = []
    prof = ctx.profile
    for k, s in ctx.src_msgs.items():
        t = ctx.tgt_msgs.get(k)
        if not t or t == s:
            continue
        scyr, slat = script_counts(s)
        tcyr, tlat = script_counts(t)
        reason, conf, lang = None, 0.0, None
        if prof.script == "cyrl" and scyr > slat and tlat > tcyr and tlat >= 4:
            latin_words = re.findall(r"[A-Za-zÀ-ÿʻʼ‘’']+", re.sub(r"\{[^{}]*\}|<[^>]*>", "", t))
            src_and_ref = s + " " + " ".join(r.get(k, "") for r in ctx.refs.values())
            ref_hit = next((loc for loc, r in ctx.refs.items() if r.get(k) == t), None)
            if ref_hit == "uz":
                lang, conf = "Uzbek", 0.97
            elif ref_hit == "en":
                lang, conf = "English", 0.9
            elif ref_hit:
                lang, conf = ref_hit, 0.9
            elif UZ_MARKERS.search(t) and re.search(r"[‘’ʻʼ]|\b\w+(lar|ni|ga|dan|ning|ish|siz)\b", t):
                lang, conf = "Uzbek", 0.95
            elif PT_ES_MARKERS.search(t):
                lang, conf = "Portuguese/Spanish", 0.95
            elif EN_MARKERS.search(t) or any(r.get(k) == t for r in ctx.refs.values()):
                lang, conf = "English", 0.8
            else:
                lang, conf = "Latin-script (non-Kazakh)", 0.6
            if ref_hit:
                reason = f"identical to the '{ref_hit}' reference translation"
            if all(w.lower() in s.lower() for w in latin_words) and lang != "Uzbek":
                conf -= 0.3   # brand names carried over from source
            if t.isupper() and len(latin_words) <= 3 and lang == "English":
                conf -= 0.15  # probably a stylised event name/logo
            if tcyr >= 3 and re.search(f"[{prof.special_letters or 'әғқңөұүһі'}]", t) and not ref_hit:
                conf -= 0.4   # Latin brand + Kazakh grammar ("MIDAS CREW ЖИНАҒЫ") is fine
            if re.search(r"(english|_en$|latin)", k, re.I):
                conf -= 0.5   # the key itself says the value is meant to be English
        elif prof.code == "kk" and re.search(r"[ЎўҲҳ]", t):
            lang, conf = "Uzbek Cyrillic", 0.9
        elif prof.script == "latn" and tcyr > tlat and tcyr >= 3:
            lang = "Kazakh" if re.search(r"[ӘәҒғҚқҢңӨөҰұҮүҺһІі]", t) else "Russian/Cyrillic"
            conf = 0.9 if t == s or lang == "Kazakh" else 0.85
        if lang and conf >= 0.3:
            ev = f"text is {lang}, not {prof.code}: «{t[:60]}»" + (f" ({reason})" if reason else "")
            out.append(ctx.finding(k, "wrong_language", "high" if conf >= 0.8 else "medium", ev,
                                   ctx.tm_suggestion(k), conf, "script_language_id"))
    return out


# ==========================================================================

ALL_CHECKS: Dict[str, Callable[[Context], List[Finding]]] = {
    "untranslated": check_untranslated,
    "same_as_source": check_same_as_source,
    "placeholder_mismatch": check_placeholders,
    "plural_mismatch": check_plurals,
    "glossary_violation": check_glossary,
    "length_overflow": check_length,
    "machine_translation_suspect": check_mt,
    "inconsistent_term": check_inconsistent,
    "suffix_harmony": check_suffix,
    "orthography": check_orthography,
    "wrong_language": check_wrong_language,
}


def run_all(ctx: Context, only: Optional[Set[str]] = None) -> List[Finding]:
    findings: List[Finding] = []
    for name, fn in ALL_CHECKS.items():
        if only and name not in only:
            continue
        findings.extend(fn(ctx))
    return findings
