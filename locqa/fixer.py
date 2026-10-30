"""Apply reviewed fixes to a target ARB and validate it like ``flutter gen-l10n`` would.

Every changed string gets an ``@key`` metadata block::

    "@laUnknownNetworkError": {"x-locqa": {"reviewed_by_human": true, "reviewer": "Aigerim",
                                "issue_types": ["same_as_source"], "original": "Неизвестная ошибка"}}

``x-`` attributes are allowed by the ARB spec and ignored by gen-l10n, so the file
still compiles. A CSV change log is written alongside for spreadsheets.
"""
from __future__ import annotations

import csv
import json
from typing import Dict, List, Optional, Tuple

from . import icu
from .arb import ArbFile, is_message_key, loads, patch


def load_decisions(path: Optional[str]) -> Dict[str, dict]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("decisions", data)


def plan_changes(src: ArbFile, tgt: ArbFile, findings: List[dict], decisions: Dict[str, dict],
                 apply_unreviewed: bool = False, min_confidence: float = 0.8) -> List[dict]:
    """Return a list of change records {key, new, original, reviewed, reviewer, issue_types, reason}."""
    src_msgs, tgt_msgs = src.messages, tgt.messages
    by_key: Dict[str, List[dict]] = {}
    for f in findings:
        by_key.setdefault(f["key"], []).append(f)
    changes = []
    for key in list(by_key) + [k for k in decisions if k not in by_key]:
        fs = by_key.get(key, [])
        types = sorted({f["issue_type"] for f in fs}) or [decisions.get(key, {}).get("issue_type", "manual")]
        d = decisions.get(key)
        if d:
            if d.get("verdict") == "reject" or d.get("text") is None:
                continue
            new, reviewed, reviewer = d["text"], True, d.get("reviewer") or "native-speaker"
        else:
            if not apply_unreviewed:
                continue
            cand = [f for f in fs if f.get("suggestion") and f["confidence"] >= min_confidence]
            if not cand:
                continue
            best = max(cand, key=lambda f: f["confidence"])
            new, reviewed, reviewer = best["suggestion"], False, None
        if key not in src_msgs:
            continue
        original = tgt_msgs.get(key)
        if new == original:
            continue
        changes.append({"key": key, "new": new, "original": original, "reviewed": reviewed,
                        "reviewer": reviewer, "issue_types": types})
    return changes


def apply_changes(src: ArbFile, tgt: ArbFile, changes: List[dict], inline_meta: bool = True) -> str:
    order = [e.key for e in src.entries if is_message_key(e.key)]
    tgt_keys = set(tgt.messages)
    values, add_after, meta = {}, {}, {}
    for c in changes:
        k = c["key"]
        values[k] = c["new"]
        if k not in tgt_keys:
            # anchor = closest preceding source key that exists in target
            idx = order.index(k)
            anchor = next((order[i] for i in range(idx - 1, -1, -1) if order[i] in tgt_keys), None)
            add_after[k] = anchor
        info = {"reviewed_by_human": bool(c["reviewed"]), "issue_types": c["issue_types"],
                "original": c["original"]}
        if c.get("reviewer"):
            info["reviewer"] = c["reviewer"]
        meta[k] = {"x-locqa": info}
    return patch(tgt, values, add_after=add_after, metadata=meta if inline_meta else None)


def write_changelog(path: str, changes: List[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "issue_types", "original", "fixed", "reviewed_by_human", "reviewer"])
        for c in changes:
            w.writerow([c["key"], ";".join(c["issue_types"]), c["original"] if c["original"] is not None else "",
                        c["new"], "yes" if c["reviewed"] else "no", c.get("reviewer") or ""])


# --------------------------------------------------------------------------
# gen-l10n compatible validation
# --------------------------------------------------------------------------

def validate(template: ArbFile, target: ArbFile) -> Tuple[List[str], List[str]]:
    """Errors that would break ``flutter gen-l10n`` + warnings. Mirrors its rules:
    valid JSON object; every message parses as ICU; translation placeholders must exist in
    the template; plural/select need an ``other`` case; @key metadata must be an object."""
    errors, warns = [], []
    tmsgs = template.messages
    for e in target.entries:
        if e.key.startswith("@@"):
            continue
        if e.key.startswith("@"):
            if not isinstance(e.value, dict):
                errors.append(f"line {e.line}: metadata {e.key} must be a JSON object")
            continue
        if not isinstance(e.value, str):
            errors.append(f"line {e.line}: message {e.key} must be a string")
            continue
        try:
            msg = icu.parse(e.value)
        except icu.IcuError as exc:
            errors.append(f"line {e.line}: {e.key}: ICU syntax error: {exc}")
            continue
        if e.key in tmsgs:
            allowed = icu.placeholders(tmsgs[e.key])
            extra = msg.placeholder_names() - allowed
            if extra:
                errors.append(f"line {e.line}: {e.key}: placeholder(s) {sorted(extra)} not defined in template")
            missing = allowed - msg.placeholder_names()
            if missing:
                warns.append(f"line {e.line}: {e.key}: placeholder(s) {sorted(missing)} unused in translation")
        else:
            warns.append(f"line {e.line}: {e.key}: not in template (ignored by gen-l10n)")
    for k in tmsgs:
        if k not in target.messages:
            warns.append(f"{k}: missing translation (falls back to template)")
    return errors, warns
