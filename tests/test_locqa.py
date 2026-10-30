import json
import os
import subprocess
import sys

import pytest

from locqa import arb, icu
from locqa.checks import (Context, check_glossary, check_length, check_orthography, check_placeholders,
                          check_plurals, check_same_as_source, check_suffix, check_untranslated,
                          check_wrong_language, check_inconsistent, check_mt)
from locqa.engine import analyse
from locqa.fixer import apply_changes, plan_changes, validate
from locqa.glossary import Glossary
from locqa.langs import kk_choose_affix, kk_rephrase_affix, kk_to_latin
from locqa.translit import translit_text

HERE = os.path.dirname(__file__)
FX = os.path.join(HERE, "fixtures")
ROOT = os.path.dirname(HERE)


@pytest.fixture()
def ctx():
    src = arb.load(os.path.join(FX, "ru.arb"))
    tgt = arb.load(os.path.join(FX, "kk.arb"))
    refs = {"uz": arb.load(os.path.join(FX, "uz.arb")).messages}
    gl = Glossary.load(os.path.join(ROOT, "glossary.csv"))
    return Context.build(src, tgt, glossary=gl, refs=refs)


def keys(findings, typ=None):
    return {f.key for f in findings if typ is None or f.issue_type == typ}


# ---------------------------------------------------------------- arb
def test_duplicates_last_wins_and_are_reported():
    a = arb.load(os.path.join(FX, "kk.arb"))
    assert a.messages["save"] == "Сақтау"
    assert "save" in a.duplicates()
    assert a.section_of()["phrase"] == "COMMON"


def test_patch_minimal_diff_and_insert():
    a = arb.load(os.path.join(FX, "kk.arb"))
    out = arb.patch(a, {"garage": "Гараж!", "missing": "Инвентарь"}, add_after={"missing": "price"},
                    metadata={"garage": {"x-locqa": {"reviewed_by_human": True}}})
    b = arb.loads(out)
    assert b.messages["garage"] == "Гараж!"
    assert b.messages["missing"] == "Инвентарь"
    assert b.metadata["garage"]["x-locqa"]["reviewed_by_human"] is True
    # untouched lines are byte-identical
    assert out.count("\n") >= a.text.count("\n")
    assert '"save": "Сақтау"' in out


# ---------------------------------------------------------------- icu
def test_icu_plural_parse():
    m = icu.parse("{count, plural, =0{нет} one{# зат} other{# зат}}")
    assert m.placeholder_names() == {"count"}
    with pytest.raises(icu.IcuError):
        icu.parse("{count, plural, one{# зат}}")       # no other
    with pytest.raises(icu.IcuError):
        icu.parse("Hello {name")


# ---------------------------------------------------------------- checks
def test_untranslated(ctx):
    f = check_untranslated(ctx)
    assert "missing" in keys(f)


def test_same_as_source(ctx):
    f = check_same_as_source(ctx)
    k = keys(f)
    assert "phrase" in k and "unknownError" in k
    assert "garage" not in k                        # accepted loanword
    s = next(x for x in f if x.key == "unknownError")
    assert s.suggestion == "Белгісіз қате"          # translation memory


def test_placeholders_and_repair(ctx):
    f = {x.key: x for x in check_placeholders(ctx)}
    assert f["greet"].suggestion == "Сәлем, {name}!"
    assert "price" not in f


def test_plural_categories(ctx):
    f = check_plurals(ctx)
    assert "items" in keys(f)
    assert "few" in next(x for x in f if x.key == "items").evidence


def test_suffix_harmony(ctx):
    f = check_suffix(ctx)
    g = next(x for x in f if x.key == "giftTo")
    assert g.suggestion == "{name} ойыншысына сыйлау"


def test_orthography_lookalike(ctx):
    f = check_orthography(ctx)
    g = next(x for x in f if x.key == "allPrizes")
    assert g.suggestion == "Барлық сыйлық"


def test_length_padding(ctx):
    f = check_length(ctx)
    g = next(x for x in f if x.key == "top")
    assert g.suggestion == "Үсті" and g.confidence > 0.9


def test_glossary(ctx):
    f = check_glossary(ctx)
    assert {"family", "skins"} <= keys(f)
    s = next(x for x in f if x.key == "skins")
    assert s.suggestion == "Скиндер"


def test_inconsistent(ctx):
    f = check_inconsistent(ctx)
    assert "cancel4" in keys(f, "inconsistent_term")


def test_wrong_language_latin():
    src = arb.loads('{"@@locale":"ru","a":"Ошибка авторизации","b":"Удалить профиль?"}')
    tgt = arb.loads('{"@@locale":"kk","a":"Avtorizatsiya xatosi","b":"Para excluir o perfil do jogo"}')
    ref = {"uz": {"a": "Avtorizatsiya xatosi"}}
    f = {x.key: x for x in check_wrong_language(Context.build(src, tgt, refs=ref))}
    assert "Uzbek" in f["a"].evidence and "Portuguese" in f["b"].evidence


def test_engine_rank_and_threshold(ctx):
    res = analyse(ctx, min_confidence=0.5)
    assert res and res[0].severity == "high"
    assert all(r.confidence >= 0.5 for r in res)


# ---------------------------------------------------------------- kazakh helpers
@pytest.mark.parametrize("word,case,affix", [
    ("Айгүл", "dat", "ге"), ("Асқар", "dat", "ға"), ("Марат", "dat", "қа"), ("Бек", "dat", "ке"),
    ("Нұрлан", "abl", "нан"), ("Әсет", "loc", "те"), ("Дана", "gen", "ның"), ("5", "dat", "ке"),
])
def test_affix_choice(word, case, affix):
    assert kk_choose_affix(word, case) == affix


def test_rephrase():
    assert kk_rephrase_affix("{provider}", "ға") == "{provider} қызметіне"
    assert kk_rephrase_affix("{fromUser}", "дан") == "{fromUser} ойыншысынан"


def test_translit():
    assert kk_to_latin("Қазақстан Республикасы") == "Qazaqstan Respublikasy"
    assert kk_to_latin("ИВЕНТ") == "İVENT"
    assert translit_text("Сәлем, {name}!") == "Sälem, {name}!"


# ---------------------------------------------------------------- fixer
def test_fix_roundtrip_and_validate(ctx):
    src, tgt = ctx.src, ctx.tgt
    findings = [x.to_dict() for x in analyse(ctx, min_confidence=0.5)]
    decisions = {"greet": {"verdict": "accept", "text": "Сәлем, {name}!", "reviewer": "test"},
                 "missing": {"verdict": "edit", "text": "Инвентарь", "reviewer": "test"},
                 "family": {"verdict": "reject"}}
    changes = plan_changes(src, tgt, findings, decisions)
    assert {c["key"] for c in changes} == {"greet", "missing"}
    out = arb.loads(apply_changes(src, tgt, changes))
    assert out.messages["greet"] == "Сәлем, {name}!"
    assert out.metadata["greet"]["x-locqa"]["reviewed_by_human"] is True
    errors, _ = validate(src, out)
    assert not [e for e in errors if "greet" in e]


def test_validate_catches_unknown_placeholder():
    src = arb.loads('{"a": "Hi {name}"}')
    tgt = arb.loads('{"a": "Сәлем {nam}"}')
    errors, _ = validate(src, tgt)
    assert errors and "nam" in errors[0]


# ---------------------------------------------------------------- CLI
def test_cli_end_to_end(tmp_path):
    out = tmp_path / "report.json"
    r = subprocess.run([sys.executable, "-m", "locqa", "check", "--source", os.path.join(FX, "ru.arb"),
                        "--target", os.path.join(FX, "kk.arb"), "--glossary", os.path.join(ROOT, "glossary.csv"),
                        "--out", str(out)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data
    for f in data:
        for k in ("key", "issue_type", "severity", "source", "target", "evidence", "suggestion",
                  "confidence", "reviewed_by_human"):
            assert k in f
        assert f["severity"] in ("high", "medium", "low")
    assert (tmp_path / "report.html").exists()
    fixed = tmp_path / "fixed.arb"
    r = subprocess.run([sys.executable, "-m", "locqa", "fix", "--source", os.path.join(FX, "ru.arb"),
                        "--target", os.path.join(FX, "kk.arb"), "--report", str(out), "--apply-unreviewed",
                        "--out", str(fixed)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = subprocess.run([sys.executable, "-m", "locqa", "validate", "--template", os.path.join(FX, "ru.arb"),
                        "--target", str(fixed)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_fail_on(tmp_path):
    r = subprocess.run([sys.executable, "-m", "locqa", "check", "--source", os.path.join(FX, "ru.arb"),
                        "--target", os.path.join(FX, "kk.arb"), "--out", str(tmp_path / "r.json"),
                        "--fail-on", "high"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 1


def test_kk_suffix_letter_and_case():
    src = arb.loads('{"@@locale":"ru","a":"Проверьте","b":"Выбрать авто","c":"КМ/Ч"}')
    tgt = arb.loads('{"@@locale":"kk","a":"Тексеріңиз","b":"Автомобильди таңдау","c":"КМ/САғ"}')
    f = {x.key: x for x in check_orthography(Context.build(src, tgt))}
    assert f["a"].suggestion == "Тексеріңіз"
    assert f["b"].suggestion == "Автомобильді таңдау"
    assert f["c"].suggestion == "КМ/САҒ"


def test_learned_padding_needs_repetition():
    src = arb.loads('{"a":"Верх","b":"Низ","c":"Победа"}')
    tgt = arb.loads('{"a":"Үсті бөліміне өту үшін басыңыз","b":"Асты бөліміне өту үшін басыңыз","c":"Жеңіс"}')
    from locqa.checks import _learn_padding
    assert _learn_padding(Context.build(src, tgt)) == ["бөліміне өту үшін басыңыз"]
