"""locqa — localization QA for Flutter ARB files.

  locqa check     --source intl_ru.arb --target intl_kk.arb --glossary glossary.csv --out report.json
  locqa fix       --source intl_ru.arb --target intl_kk.arb --report report.json --decisions decisions.json --out intl_kk_fixed.arb
  locqa validate  --template intl_ru.arb --target intl_kk_fixed.arb
  locqa translit  --source intl_ru.arb --target intl_kk.arb --out intl_kk_Latn.arb
  locqa llm-review --source intl_ru.arb --target intl_kk.arb --out review.jsonl [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import List, Optional

from . import __version__, arb
from .checks import ISSUE_TYPES, Context
from .engine import analyse, dedupe, load_review, merge_review, rank
from .glossary import Glossary


def _refs(paths: List[str]):
    refs = {}
    for p in paths or []:
        a = arb.load(p)
        refs[a.locale or os.path.basename(p)] = a.messages
    return refs


def cmd_check(a) -> int:
    src, tgt = arb.load(a.source), arb.load(a.target)
    gl = Glossary.load(a.glossary) if a.glossary else None
    ctx = Context.build(src, tgt, glossary=gl, refs=_refs(a.ref), locale=a.locale)
    only = set(a.only.split(",")) if a.only else None
    all_f = analyse(ctx, only=only, review_files=a.review or [], min_confidence=0.0)
    if a.reviewed:
        # human decisions: rejected findings disappear, accepted/edited are marked reviewed
        from .fixer import load_decisions
        dec = load_decisions(a.reviewed)
        kept = []
        for f in all_f:
            d = dec.get(f.key)
            if d and d.get("verdict") == "reject" and d.get("issue_type") in (None, f.issue_type):
                continue
            if d and d.get("verdict") in ("accept", "edit"):
                f.reviewed_by_human = True
                if d.get("text"):
                    f.suggestion = d["text"]
            kept.append(f)
        all_f = kept
    findings = [f for f in all_f if f.confidence >= a.min_confidence]
    candidates = [f for f in all_f if f.confidence < a.min_confidence]
    meta = {
        "tool": "locqa", "tool_version": __version__,
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source_name": os.path.basename(a.source), "target_name": os.path.basename(a.target),
        "locale": ctx.profile.code, "strings_checked": len(ctx.src_msgs),
        "min_confidence": a.min_confidence, "review_files": [os.path.basename(p) for p in (a.review or [])],
        "glossary": os.path.basename(a.glossary) if a.glossary else None,
    }
    from .report import write_html, write_json
    write_json(a.out, findings, meta, candidates, ctx.warnings)
    html_path = a.html or (a.out[:-5] + ".html" if a.out.endswith(".json") else a.out + ".html")
    write_html(html_path, findings, meta, candidates, ctx.warnings)
    by = {}
    for f in findings:
        by[f.issue_type] = by.get(f.issue_type, 0) + 1
    print(f"locqa {__version__}: {len(ctx.src_msgs)} strings, {len(findings)} findings "
          f"({len(candidates)} candidates below {a.min_confidence})", file=sys.stderr)
    for t in ISSUE_TYPES:
        if by.get(t):
            print(f"  {t:30} {by[t]}", file=sys.stderr)
    print(f"  -> {a.out}\n  -> {html_path}", file=sys.stderr)
    if a.fail_on:
        sev_order = {"high": 0, "medium": 1, "low": 2}
        lim = sev_order[a.fail_on]
        bad = [f for f in findings if sev_order.get(f.severity, 3) <= lim]
        if bad:
            print(f"FAIL: {len(bad)} finding(s) at severity >= {a.fail_on}", file=sys.stderr)
            return 1
    return 0


def cmd_fix(a) -> int:
    from .fixer import apply_changes, load_decisions, plan_changes, validate, write_changelog
    src, tgt = arb.load(a.source), arb.load(a.target)
    findings = json.load(open(a.report, encoding="utf-8"))
    decisions = load_decisions(a.decisions)
    changes = plan_changes(src, tgt, findings, decisions, apply_unreviewed=a.apply_unreviewed,
                           min_confidence=a.min_confidence)
    text = apply_changes(src, tgt, changes, inline_meta=not a.no_inline_meta)
    out = arb.loads(text, a.out)
    errors, warns = validate(src, out)
    if errors and not a.force:
        print("refusing to write: the result would not compile:\n  " + "\n  ".join(errors[:20]), file=sys.stderr)
        return 2
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    log = a.changelog or (a.out[:-4] + ".changes.csv")
    write_changelog(log, changes)
    side = a.out[:-4] + ".review.json"
    with open(side, "w", encoding="utf-8") as fh:
        json.dump({c["key"]: {"reviewed_by_human": c["reviewed"], "reviewer": c.get("reviewer"),
                              "issue_types": c["issue_types"], "original": c["original"], "fixed": c["new"]}
                   for c in changes}, fh, ensure_ascii=False, indent=2)
    rev = sum(1 for c in changes if c["reviewed"])
    print(f"{len(changes)} strings changed ({rev} reviewed by a human, {len(changes) - rev} unreviewed) -> {a.out}",
          file=sys.stderr)
    print(f"change log -> {log}; validation: {len(errors)} errors, {len(warns)} warnings", file=sys.stderr)
    return 0


def cmd_validate(a) -> int:
    from .fixer import validate
    errors, warns = validate(arb.load(a.template), arb.load(a.target))
    for e in errors:
        print("ERROR  " + e)
    if a.verbose:
        for w in warns:
            print("warn   " + w)
    print(f"{len(errors)} errors, {len(warns)} warnings", file=sys.stderr)
    return 1 if errors else 0


def cmd_translit(a) -> int:
    from .translit import translit_arb
    tgt = arb.load(a.target)
    src = arb.load(a.source).messages if a.source else {}
    text, warns = translit_arb(tgt, src, max_growth=a.max_growth)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    if a.report:
        json.dump(warns, open(a.report, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"transliterated -> {a.out}; {len(warns)} length warnings", file=sys.stderr)
    return 0


def cmd_llm(a) -> int:
    from .llm import build_rows, review
    src, tgt = arb.load(a.source), arb.load(a.target)
    ref = arb.load(a.ref).messages if a.ref else {}
    only = None
    if a.keys_from:
        only = {f["key"] for f in json.load(open(a.keys_from, encoding="utf-8"))}
    rows = build_rows(src.messages, tgt.messages, ref, only)
    usage = review(rows, a.out, a.model, locale=tgt.locale or "kk", dry_run=a.dry_run,
                   price_in=a.price_in, price_out=a.price_out)
    print(json.dumps(usage, indent=2), file=sys.stderr)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="locqa", description="Localization QA for Flutter ARB files")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="find localization issues")
    c.add_argument("--source", required=True, help="source/template ARB (e.g. intl_ru.arb)")
    c.add_argument("--target", required=True, help="translated ARB to check")
    c.add_argument("--glossary", help="glossary CSV (term_ru,term_kk,term_en,comment,...)")
    c.add_argument("--out", required=True, help="report.json path (HTML written next to it)")
    c.add_argument("--html", help="explicit HTML report path")
    c.add_argument("--ref", action="append", help="reference ARB in another language (repeatable)")
    c.add_argument("--review", action="append", help="JSONL from llm-review / human review to merge (repeatable)")
    c.add_argument("--reviewed", help="decisions.json exported from the HTML review UI")
    c.add_argument("--locale", help="override target locale (default: @@locale)")
    c.add_argument("--only", help="comma-separated issue types to run")
    c.add_argument("--min-confidence", type=float, default=0.5)
    c.add_argument("--fail-on", choices=["high", "medium", "low"], help="exit 1 if findings at this severity exist (CI)")
    c.set_defaults(fn=cmd_check)

    f = sub.add_parser("fix", help="apply reviewed fixes and write a fixed ARB")
    f.add_argument("--source", required=True)
    f.add_argument("--target", required=True)
    f.add_argument("--report", required=True)
    f.add_argument("--decisions", help="decisions.json from the HTML review UI")
    f.add_argument("--out", required=True)
    f.add_argument("--changelog")
    f.add_argument("--apply-unreviewed", action="store_true",
                   help="also apply high-confidence suggestions nobody reviewed (marked reviewed_by_human=false)")
    f.add_argument("--min-confidence", type=float, default=0.85)
    f.add_argument("--force", action="store_true")
    f.add_argument("--no-inline-meta", action="store_true",
                   help="do not add @key x-locqa metadata; review flags only in <out>.review.json")
    f.set_defaults(fn=cmd_fix)

    v = sub.add_parser("validate", help="check an ARB compiles under flutter gen-l10n rules")
    v.add_argument("--template", required=True)
    v.add_argument("--target", required=True)
    v.add_argument("-v", "--verbose", action="store_true")
    v.set_defaults(fn=cmd_validate)

    t = sub.add_parser("translit", help="Kazakh Cyrillic -> Latin (2021) with length check")
    t.add_argument("--target", required=True)
    t.add_argument("--source")
    t.add_argument("--out", required=True)
    t.add_argument("--report")
    t.add_argument("--max-growth", type=float, default=1.15)
    t.set_defaults(fn=cmd_translit)

    l = sub.add_parser("llm-review", help="optional LLM reviewer pass -> JSONL for --review")
    l.add_argument("--source", required=True)
    l.add_argument("--target", required=True)
    l.add_argument("--ref")
    l.add_argument("--out", required=True)
    l.add_argument("--model", default="claude-sonnet-5-5")
    l.add_argument("--keys-from", help="only review keys present in this report.json")
    l.add_argument("--dry-run", action="store_true")
    l.add_argument("--price-in", type=float, default=0.0, help="USD per 1M input tokens")
    l.add_argument("--price-out", type=float, default=0.0, help="USD per 1M output tokens")
    l.set_defaults(fn=cmd_llm)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
