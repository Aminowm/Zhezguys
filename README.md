# locqa — Qazaq Loc QA

Localization QA for Flutter ARB files, built for the Grand Mobile × Decentrathon 6.0 case.
It works on **any** ARB pair (source → target). It has extra knowledge of Kazakh (affix harmony,
look-alike letters, Latin transliteration) and of Uzbek (Latin script, oʻ/gʻ apostrophes).

* Pure Python ≥ 3.9 standard library. No runtime dependencies. `pytest` is used only for tests.
* Implements the case contract: `locqa check --source intl_ru.arb --target intl_kk.arb --glossary glossary.csv --out report.json`
* Writes `report.json` (a top-level array in the exact finding schema), an HTML report that is also the
  native-speaker review console, and a compile-safe `intl_kk_fixed.arb` in which every changed string
  carries a `reviewed_by_human` flag.

## Install

```bash
pip install .            # or: python -m locqa ...   (no install needed)
```

## Commands

```bash
# 1. find problems  (report.json + report.html + report.meta.json)
locqa check --source intl_ru.arb --target intl_kk.arb --glossary glossary.csv --out report.json \
            [--ref intl_en.arb --ref intl_uz.arb]      # reference locales sharpen wrong_language / length
            [--review llm_review.jsonl]                # merge an LLM / human review pass
            [--reviewed decisions.json]                # apply native-speaker verdicts to the report
            [--min-confidence 0.6] [--fail-on high]    # noise threshold; CI exit code

# 2. native speaker opens report.html: Accept / Edit / False positive per string,
#    then clicks "Export decisions.json"

# 3. write the fixed file. Only reviewed strings are applied by default.
locqa fix --source intl_ru.arb --target intl_kk.arb --report report.json \
          --decisions decisions.json --out intl_kk_fixed.arb  [--apply-unreviewed]

# 4. compile check with the same rules as flutter gen-l10n (ICU syntax, placeholders vs template)
locqa validate --template intl_ru.arb --target intl_kk_fixed.arb

# bonus
locqa check --source intl_ru.arb --target intl_uz.arb --glossary glossary.csv --out report_uz.json  # Uzbek
locqa translit --source intl_ru.arb --target intl_kk.arb --out intl_kk_Latn.arb --report translit_len.json
locqa llm-review --source intl_ru.arb --target intl_kk.arb --ref intl_en.arb --out review.jsonl --dry-run
```

The GitHub Action in `.github/workflows/locqa.yml` runs `validate` and `check --fail-on high` on every
pull request that touches `*.arb`. It puts the summary in the job summary and uploads the HTML reports.

## Finding schema (`report.json`)

```json
{
  "key": "inventoryMailSubsectionTop",
  "issue_type": "length_overflow",
  "severity": "high",
  "source": "Верх",
  "target": "Үсті бөліміне өту үшін басыңыз",
  "evidence": "30 chars vs 4 in source (x7.5); short UI element padded with filler phrase «бөліміне өту үшін басыңыз»",
  "suggestion": "Үсті",
  "confidence": 0.98,
  "reviewed_by_human": false,
  "line": 4251, "section": "INVENTORY", "detector": "padding_phrase+llm_review"
}
```

`line`, `section` and `detector` are extra fields for explainability. Findings below `--min-confidence`
are not dropped. They go to `report.meta.json → candidates_below_threshold` and show up in the HTML
behind the "show candidates" toggle, so a reviewer can still triage them without adding noise to the report.

## What each check does

| issue_type | rule (all evidence strings say which rule fired and with what numbers) |
|---|---|
| `untranslated` | key missing in target, or empty value. Empty prefixes/particles that another locale also leaves empty get low confidence. |
| `same_as_source` | target == source. Brands, numbers, Latin, and a Kazakh loanword list are exempt. Confidence rises with phrase length, Russian function words or verb forms, and when translation memory has a real translation of the same text. |
| `placeholder_mismatch` | ICU placeholder sets, printf `%s`, and markup tags `<g>` are compared. Auto-repair restores stripped braces (`time` → `{time}`) and fixes misspelled names (`{platformColo}` → `{platformColor}`) by edit distance. |
| `plural_mismatch` | a small ICU parser. It checks for a missing `other` form, CLDR categories that are invalid for the target (e.g. `few` in `kk`), and unknown placeholders inside forms. |
| `glossary_violation` | `glossary.csv` defines a source regex, plus allowed, forbidden and tolerated target renderings, plus auto-replacements. |
| `length_overflow` | short UI strings (≤25 chars, or key looks like button/tab/title). Filler phrases are detected explicitly. The ratio is calibrated on the file's median expansion and damped when reference locales are long too. |
| `machine_translation_suspect` | Russian grammar inside Kazakh (Russian function words or inflections next to Kazakh words) and known calques (e.g. «скины» → «терілер»). Plus the LLM reviewer pass. |
| `inconsistent_term` | (a) the same source text translated differently: the minority is flagged when ≥3 uses and ≥70 % agree; (b) statistical term alignment (a source stem → target stem in ≥90 % of strings), where the outliers become candidates. The suggestion is not taken from translation memory when the majority itself is flagged. |
| `suffix_harmony` | a Kazakh case affix glued to a placeholder (`{name}-ға`, `{max}-нан`). The tool proposes a harmony-independent rephrasing with a head noun (`{name} ойыншысына`, `{provider} қызметіне`). For runtime handling, `docs/dart/kk_affix.dart` has the affix rule (mirrored in Python and unit-tested). |
| `orthography` | Latin homoglyphs inside Cyrillic words; Kazakh letters typed as Russian look-alikes (`сыйлык` → `сыйлық`, `жане` → `және`), learned from the file's own vocabulary; Uzbek oʻ/gʻ apostrophe consistency. |
| `wrong_language` | script and language ID (Uzbek markers, Portuguese/Spanish, English), plus an exact match against reference locales (`--ref`). |

File-level problems (keys defined twice with different values, extra keys) go to `warnings`.

## Tests

```bash
python -m pytest -q        # 30 tests: parser, every check, affix rules, translit, fixer, CLI end-to-end
```

## Data and results

The organizers' raw ARB files are confidential and are **not** committed here (`data/` is gitignored,
per the case rules — "data in it there must not be"). Everything this tool *produced* from them is
committed as the required deliverables:

* `out/report.json` / `out/report.html` — the findings (349 at `--min-confidence 0.6`, 338 more candidates
  below threshold) and the review console.
* `out/intl_kk_fixed.arb` — the corrected Kazakh file. 313 strings changed; every one carries
  `reviewed_by_human: true` and the reviewer's name after a native Kazakh speaker went through
  `report.html` (see `work/decisions.json`: 269 accepted, 45 edited, 2 rejected as false positives).
  Re-running `check` against the fixed file drops the finding count from 349 to 5.
* `glossary.csv` — the game-term glossary used by `glossary_violation`.
* `work/` — the raw LLM review and verification passes (`kk_llm_review.jsonl`, `kk_verify.jsonl`) that fed
  into the report, plus the exported human decisions.
* `docs/METHODOLOGY.md` — how findings were detected and fixed, and the limitations we're aware of.
