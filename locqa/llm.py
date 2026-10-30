"""Optional LLM review pass (Anthropic Messages API, stdlib only).

The LLM is used as a *reviewer*, never as a bulk re-translator: it sees source + target
(+ English reference) in batches and returns only strings it considers wrong, with an
issue type, evidence and a minimal corrected string. Output is JSONL that the main
``check`` command merges with rule findings (``--review``). Every suggestion still
goes through the native-speaker review UI before it lands in the fixed ARB.

Cost is tracked from the API's ``usage`` field and printed at the end; ``--dry-run``
estimates it without calling the API (≈ 1 token per 2.5 chars for Cyrillic).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from typing import Dict, Iterable, List, Optional

from .checks import ISSUE_TYPES

API_URL = "https://api.anthropic.com/v1/messages"

PROMPT = """You are a native Kazakh localization QA reviewer for a GTA-style mobile RP game (Grand Mobile).
For each line you get: key, RU (source), TARGET ({locale}), EN (reference). Report ONLY real problems
in TARGET. Allowed issue_type values: {types}.
Game glossary: фракция->фракция, семья->отбасы, Grand Coins/GC unchanged, бизнес->бизнес, варн->варн/ескерту,
скин->скин (not тері), кейс->кейс, инвентарь->инвентарь, ивент->ивент, игрок->ойыншы.
Do not flag acceptable synonyms, brands, names, numbers or established loanwords.
Reply with JSON only: a list of objects {{"key","issue_type","severity","evidence","suggestion","confidence"}}.
"suggestion" is the complete corrected string; keep every placeholder ({{x}}, %s), tag (<g></g>) and line break.
Return [] if everything is fine.

LINES:
{lines}"""


def batches(rows: List[dict], max_chars: int = 12000) -> Iterable[List[dict]]:
    cur, size = [], 0
    for r in rows:
        line = json.dumps(r, ensure_ascii=False)
        if cur and size + len(line) > max_chars:
            yield cur
            cur, size = [], 0
        cur.append(r)
        size += len(line)
    if cur:
        yield cur


def build_rows(src_msgs: Dict[str, str], tgt_msgs: Dict[str, str], ref: Dict[str, str],
               only_keys: Optional[set] = None) -> List[dict]:
    rows = []
    for k, s in src_msgs.items():
        if only_keys and k not in only_keys:
            continue
        t = tgt_msgs.get(k)
        if t is None:
            continue
        rows.append({"key": k, "RU": s, "TARGET": t, "EN": ref.get(k, "")})
    return rows


def _call(model: str, prompt: str, api_key: str, max_tokens: int = 4096) -> dict:
    body = json.dumps({"model": model, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read())
        except Exception:  # pragma: no cover - network
            if attempt == 3:
                raise
            time.sleep(2 ** attempt * 3)
    raise RuntimeError("unreachable")


def parse_reply(text: str) -> List[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        items = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [i for i in items if isinstance(i, dict) and i.get("issue_type") in ISSUE_TYPES and i.get("key")]


def review(rows: List[dict], out_path: str, model: str, locale: str = "kk", dry_run: bool = False,
           price_in: float = 0.0, price_out: float = 0.0, cache_dir: str = ".locqa_cache") -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    os.makedirs(cache_dir, exist_ok=True)
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "est_input_tokens": 0}
    found = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for batch in batches(rows):
            lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in batch)
            prompt = PROMPT.format(locale=locale, types=", ".join(ISSUE_TYPES), lines=lines)
            usage["est_input_tokens"] += int(len(prompt) / 2.5)
            if dry_run:
                continue
            if not api_key:
                raise SystemExit("ANTHROPIC_API_KEY is not set (use --dry-run to estimate cost)")
            h = hashlib.sha256((model + prompt).encode()).hexdigest()[:24]
            cpath = os.path.join(cache_dir, h + ".json")
            if os.path.exists(cpath):
                resp = json.load(open(cpath, encoding="utf-8"))
            else:
                resp = _call(model, prompt, api_key)
                json.dump(resp, open(cpath, "w", encoding="utf-8"), ensure_ascii=False)
                usage["requests"] += 1
                usage["input_tokens"] += resp.get("usage", {}).get("input_tokens", 0)
                usage["output_tokens"] += resp.get("usage", {}).get("output_tokens", 0)
            text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
            valid = {r["key"] for r in batch}
            for item in parse_reply(text):
                if item["key"] in valid:
                    item["origin"] = f"llm:{model}"
                    out.write(json.dumps(item, ensure_ascii=False) + "\n")
                    found += 1
    tin = usage["input_tokens"] or usage["est_input_tokens"]
    tout = usage["output_tokens"] or int(tin * 0.15)
    usage["findings"] = found
    usage["cost_usd"] = round(tin / 1e6 * price_in + tout / 1e6 * price_out, 4)
    usage["estimated"] = dry_run or not usage["requests"]
    return usage
