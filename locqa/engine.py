"""Orchestration: run checks, merge external (LLM / human) review, dedupe, rank."""
from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional, Set

from .checks import ISSUE_TYPES, Context, Finding, run_all

SEV_RANK = {"high": 0, "medium": 1, "low": 2}

# When several detectors fire on one key, keep one finding per (key, type) and
# drop types that are explained by a stronger one.
SUBSUMED = {
    # a Russian string copied over is also "wrong language" for an uz file; keep one
    ("same_as_source", "wrong_language"): "same_as_source",
    ("untranslated", "same_as_source"): "untranslated",
    ("length_overflow", "machine_translation_suspect"): "length_overflow",   # filler padding wins
}


def load_review(path: str) -> List[dict]:
    """JSONL produced by `locqa llm-review` or by a human reviewer."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def merge_review(ctx: Context, findings: List[Finding], review_rows: Iterable[dict],
                 origin: str = "llm_review") -> List[Finding]:
    by = {(f.key, f.issue_type): f for f in findings}
    for r in review_rows:
        k, typ = r.get("key"), r.get("issue_type")
        verdict = r.get("verdict")
        if k not in ctx.src_msgs:
            continue
        if verdict in ("reject", "false_positive", "reject_fix", "ok", "improve"):
            # second-opinion verdicts on existing findings (typ optional = all types on the key)
            hit = [kt for kt in by if kt[0] == k and (not typ or kt[1] == typ)]
            for kt in hit:
                f = by[kt]
                if verdict in ("reject", "false_positive"):
                    del by[kt]
                elif verdict == "reject_fix":
                    f.suggestion = None
                    f.evidence += f" | {origin}: no safe automatic fix ({r.get('note', '')})"
                elif r.get("text") is not None:
                    f.suggestion = r["text"]
                    if r.get("reviewed_by_human"):
                        f.reviewed_by_human = True
                    f.detector += f"+{origin}:{verdict}"
            continue
        if typ not in ISSUE_TYPES:
            continue
        existing = by.get((k, typ))
        sugg = r.get("suggestion")
        if existing:
            # confirmation from an independent reviewer raises confidence; prefer its suggestion
            existing.confidence = min(0.99, max(existing.confidence, float(r.get("confidence", 0.7))) + 0.03)
            if sugg and sugg != existing.target:
                existing.suggestion = sugg
            if r.get("evidence") and r["evidence"] not in existing.evidence:
                existing.evidence += f" | {origin}: {r['evidence']}"
            existing.detector += f"+{origin}"
            if r.get("reviewed_by_human"):
                existing.reviewed_by_human = True
            continue
        # a key may already carry a different type from rules — keep both only if
        # the reviewer adds a genuinely different problem
        f = ctx.finding(k, typ, r.get("severity", "medium"), f"{origin}: {r.get('evidence', '')}",
                        sugg if sugg != ctx.tgt_msgs.get(k) else None,
                        float(r.get("confidence", 0.7)), origin)
        f.reviewed_by_human = bool(r.get("reviewed_by_human", False))
        by[(k, typ)] = f
    return list(by.values())


def dedupe(findings: List[Finding]) -> List[Finding]:
    by_key: Dict[str, List[Finding]] = {}
    for f in findings:
        by_key.setdefault(f.key, []).append(f)
    out = []
    for k, fs in by_key.items():
        types = {f.issue_type for f in fs}
        drop: Set[str] = set()
        for (a, b), keep in SUBSUMED.items():
            if a in types and b in types:
                drop.add(b if keep == a else a)
        # a long *mistranslation* is a meaning problem, not a layout one: when the length
        # rule fired only on the ratio and a reviewer found a meaning error, keep the latter
        if "machine_translation_suspect" in types and "length_overflow" in types:
            ratio_only = all(f.detector.startswith("length_ratio") for f in fs if f.issue_type == "length_overflow")
            if ratio_only:
                drop.discard("machine_translation_suspect")
                drop.add("length_overflow")
        kept = [f for f in fs if f.issue_type not in drop]
        out.extend(kept)
    return out


def _drop_bad_tm(findings: List[Finding]) -> List[Finding]:
    """A translation-memory suggestion is only as good as the majority it copies. If the
    majority text is itself flagged elsewhere (e.g. «Отмена»→«БАСТАУ»), drop the TM finding
    and its suggestion — the majority keys carry their own findings."""
    bad = {f.target.strip().lower() for f in findings
           if f.confidence >= 0.7 and f.issue_type != "inconsistent_term" and f.target}
    out = []
    for f in findings:
        if f.detector.startswith("tm_majority") and f.suggestion and f.suggestion.strip().lower() in bad:
            continue
        if f.suggestion and f.suggestion.strip().lower() in bad and f.issue_type in ("same_as_source", "untranslated", "wrong_language"):
            f.suggestion = None
        out.append(f)
    return out


def rank(findings: List[Finding]) -> List[Finding]:
    return sorted(findings, key=lambda f: (SEV_RANK.get(f.severity, 3), -f.confidence, f.line, f.key))


def analyse(ctx: Context, only: Optional[Set[str]] = None, review_files: Iterable[str] = (),
            min_confidence: float = 0.5) -> List[Finding]:
    findings = run_all(ctx, only)
    for p in review_files:
        origin = "verify" if "verif" in p.lower() else "human_review" if "human" in p.lower() else "llm_review"
        findings = merge_review(ctx, findings, load_review(p), origin=origin)
    findings = dedupe(_drop_bad_tm(findings))
    findings = [f for f in findings if f.confidence >= min_confidence]
    for f in findings:
        f.severity = _severity(f)
    return rank(findings)


def _severity(f: Finding) -> str:
    """Severity = user impact, not confidence. Broken runtime > wrong meaning > cosmetic."""
    t = f.issue_type
    if t in ("placeholder_mismatch", "plural_mismatch", "wrong_language", "untranslated"):
        return "high"
    if t == "same_as_source":
        return "high" if f.confidence >= 0.8 else "medium"
    if t == "length_overflow":
        return "high" if "filler" in f.evidence else f.severity
    if t in ("machine_translation_suspect",):
        return "high" if f.confidence >= 0.85 else "medium"
    if t == "orthography":
        return "medium" if f.confidence >= 0.8 else "low"
    return f.severity
