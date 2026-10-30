#!/usr/bin/env bash
# Reproduce every deliverable for the Grand Mobile case from the raw ARB files.
set -euo pipefail
cd "$(dirname "$0")/.."
D=data; W=work; O=out; mkdir -p "$O"
REVIEWS=(--review "$W/kk_llm_review.jsonl" --review "$W/kk_manual_llm.jsonl" --review "$W/kk_verify.jsonl")
DECISIONS=()
[ -f "$W/decisions.json" ] && DECISIONS=(--decisions "$W/decisions.json")   # exported from report.html by the native speaker
REVIEWED=()
[ -f "$W/decisions.json" ] && REVIEWED=(--reviewed "$W/decisions.json")

python3 -m locqa check --source $D/intl_ru.arb --target $D/intl_kk.arb --glossary glossary.csv \
  --ref $D/intl_en.arb --ref $D/intl_uz.arb "${REVIEWS[@]}" "${REVIEWED[@]}" --min-confidence 0.6 --out $O/report.json
python3 -m locqa fix --source $D/intl_ru.arb --target $D/intl_kk.arb --report $O/report.json "${DECISIONS[@]}" \
  --apply-unreviewed --min-confidence 0.6 --out $O/intl_kk_fixed.arb
python3 -m locqa validate --template $D/intl_ru.arb --target $O/intl_kk_fixed.arb
python3 -m locqa check --source $D/intl_ru.arb --target $O/intl_kk_fixed.arb --glossary glossary.csv \
  --ref $D/intl_en.arb --ref $D/intl_uz.arb --min-confidence 0.6 --out $O/recheck_fixed.json
# bonus
python3 -m locqa check --source $D/intl_ru.arb --target $D/intl_uz.arb --glossary glossary.csv \
  --ref $D/intl_en.arb --min-confidence 0.6 --out $O/report_uz.json
python3 -m locqa translit --source $D/intl_ru.arb --target $O/intl_kk_fixed.arb --out $O/intl_kk_Latn.arb \
  --report $O/translit_length_report.json
python3 -m locqa llm-review --source $D/intl_ru.arb --target $D/intl_kk.arb --ref $D/intl_en.arb \
  --out $O/llm_dry_run.jsonl --dry-run 2> $O/llm_cost_estimate.json || true
