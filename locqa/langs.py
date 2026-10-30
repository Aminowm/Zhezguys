"""Language profiles: script, special letters, morphology helpers.

Everything language-specific lives here so the checks stay generic and the
tool works on any ARB pair (unknown locales fall back to a neutral profile).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

CYR = "А-Яа-яЁё"
KK_EXTRA = "ӘәҒғҚқҢңӨөҰұҮүҺһІі"
UZ_CYR_EXTRA = "ЎўҚқҒғҲҳ"
WORD_RE = re.compile(rf"[{CYR}{KK_EXTRA}{UZ_CYR_EXTRA}A-Za-zʻʼ‘’'`ʹ-]+")
CYR_WORD_RE = re.compile(rf"[{CYR}{KK_EXTRA}{UZ_CYR_EXTRA}]+")

# Russian function words: a Kazakh/Uzbek sentence containing several of them is
# Russian text or a half-translated calque.
RU_FUNCTION_WORDS = set("""и в во на для с со по что это как от до за из к ко у о об при или но же бы ли уже
вы ты мы он она они вас вам ваш ваша ваше ваши твой твоя твои свой своя свои есть был была будет будут
можно нужно нельзя необходимо чтобы если когда также только всё все всех тебя тебе себя его её их
который которая которые этот эта эти этого чем тем где куда здесь сейчас""".split())

# Words that end in typical Russian inflections (used for "Russian grammar inside
# Kazakh" detection). Applied only to words WITHOUT Kazakh-specific letters.
RU_ENDINGS = ("ться", "тся", "ость", "ение", "ений", "ание", "аний", "ющий", "ющая", "ющие",
              "ными", "ного", "ному", "ыми", "ого", "ему", "ешь", "ишь", "ете", "ите", "уйте",
              "айте", "ийся", "аяся", "ые", "ая", "яя", "ий", "ый")


@dataclass
class Profile:
    code: str
    script: str                     # "cyrl" | "latn"
    special_letters: str = ""       # letters that must not be replaced by look-alikes
    lookalikes: Dict[str, str] = field(default_factory=dict)  # special -> ascii/russian look-alike
    plural_categories: Set[str] = field(default_factory=lambda: {"one", "other"})
    loanwords: Set[str] = field(default_factory=set)  # lower-case words legitimately equal to RU
    length_ratio: float = 1.0       # median target/source length ratio (calibrated per file)


# --------------------------------------------------------------------------
# Kazakh
# --------------------------------------------------------------------------

KK_LOOKALIKE = {  # Kazakh letter -> what careless typing / Russian keyboard produces
    "ә": "а", "ғ": "г", "қ": "к", "ң": "н", "ө": "о", "ұ": "у", "ү": "у", "һ": "х", "і": "и",
    "Ә": "А", "Ғ": "Г", "Қ": "К", "Ң": "Н", "Ө": "О", "Ұ": "У", "Ү": "У", "Һ": "Х", "І": "И",
}
LATIN_HOMOGLYPHS = {  # latin -> cyrillic that looks identical
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у", "i": "і", "k": "к",
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М", "O": "О", "P": "Р",
    "T": "Т", "X": "Х", "I": "І",
}

# Loanwords / internationalisms that are legitimately identical in RU and KK UI.
KK_LOANWORDS = set("""
ок ok мин сек м с км кг л мин. макс. бонус промокод профиль аккаунт логин графика кабинет бизнес фракция
вокзал инкассатор онлайн офлайн дилер аналог неон капот бампер бак багаж авто стайлинг стерео нитро
гидравлика спойлер турбонаддув чип-тюнинг модификация кубок алхимия аирдроп лава энергия металл нейрон
картон маска пластик селитра алмаз самокат колба айсберг букет голем кактус чемпионат ранг максимум
минимум департамент банкомат аксессуар прогресс рп нрп мегафон нуга турмалин ставка телефон арена класс
гараж рейтинг эвакуатор таймер финиш дерби картинг паспорт нитро ивент экспедиция квест автоквест
бустер торт клоун фейерверк лига кулдаун контрабанда маршрут аукцион планшет база витрина элита премиум
инвентарь катушка лимит карта сейф контейнер тюнинг эксклюзив сервер терминал лидерборд бинго тир финал
команда гол раунд сканер рулетка фестиваль лотерея колода математика информатика физика география
биология директор бонус банк локация кейс скин пасс стандарт клаксоны кепки баскет шефбургер лента
моментальный фейк вайб плюс минус циркуль интернет wi-fi id vip gc
баланс трансмиссия шоколад статистика
""".split())

KK_PROFILE = Profile(
    code="kk", script="cyrl", special_letters="ӘәҒғҚқҢңӨөҰұҮүҺһІі",
    lookalikes=KK_LOOKALIKE, plural_categories={"one", "other"}, loanwords=KK_LOANWORDS,
)

KK_BACK_VOWELS = set("аоұыя")
KK_FRONT_VOWELS = set("әөүіеэ")
KK_VOWELS = KK_BACK_VOWELS | KK_FRONT_VOWELS | set("уию")

# Case/possessive affixes that might be glued to a placeholder. Hard-coding one
# variant is wrong because the right form depends on the substituted word.
KK_AFFIX_CASE = {
    # dative
    "ға": "dat", "ге": "dat", "қа": "dat", "ке": "dat", "на": "dat", "не": "dat", "а": "dat", "е": "dat",
    # locative
    "да": "loc", "де": "loc", "та": "loc", "те": "loc", "нда": "loc", "нде": "loc",
    # ablative
    "дан": "abl", "ден": "abl", "тан": "abl", "тен": "abl", "нан": "abl", "нен": "abl",
    # genitive
    "ның": "gen", "нің": "gen", "дың": "gen", "дің": "gen", "тың": "gen", "тің": "gen", "ың": "gen", "ің": "gen",
    # accusative
    "ны": "acc", "ні": "acc", "ды": "acc", "ді": "acc", "ты": "acc", "ті": "acc", "н": "acc", "ын": "acc", "ін": "acc",
    # instrumental
    "мен": "ins", "бен": "ins", "пен": "ins", "менен": "ins",
    # plural / possessive (also form-dependent)
    "лар": "pl", "лер": "pl", "дар": "pl", "дер": "pl", "тар": "pl", "тер": "pl",
    "ы": "poss", "і": "poss", "сы": "poss", "сі": "poss",
}
CASE_NAMES = {"dat": "dative", "loc": "locative", "abl": "ablative", "gen": "genitive",
              "acc": "accusative", "ins": "instrumental", "pl": "plural", "poss": "possessive"}

# 3rd-person possessive head nouns (already end in -ы/-і) used to rephrase
# "{x}-ға" -> "{x} <head>на". The head absorbs the affix so harmony is fixed.
# (pattern on placeholder name, head noun in poss.3 form, harmony b=back f=front)
HEAD_NOUNS: List[Tuple[str, str, str]] = [
    (r"(user|player|nick|name|recipient|owner|sender|from|to)", "ойыншы", "b"),
    (r"(price|cost|bet|sum|amount|money|gc|coins?)", "сома", "b"),
    (r"(provider|service|store|shop|platform)", "қызмет", "f"),
    (r"(level|lvl)", "деңгей", "f"),
    (r"(place|position|rank)", "орын", "b"),
    (r"(min|max|count|number|num|qty|value|limit)", "мөлшер", "f"),
    (r"(competition|season|event|act|chapter)", "кезең", "f"),
    (r"(title|item|prize|reward)", "атау", "b"),
    (r".*", "мән", "f"),
]


def kk_poss3(noun: str, harmony: str) -> str:
    """ойыншы -> ойыншысы, қызмет -> қызметі, сома -> сомасы."""
    last = noun[-1]
    if last in KK_VOWELS:
        return noun + ("сы" if harmony == "b" else "сі")
    return noun + ("ы" if harmony == "b" else "і")


def kk_case_after_poss3(stem_poss3: str, case: str, harmony: str) -> str:
    """Attach a case ending to a 3rd-person-possessive form (pronominal 'н' declension)."""
    b = harmony == "b"
    end = {
        "dat": "на" if b else "не",
        "loc": "нда" if b else "нде",
        "abl": "нан" if b else "нен",
        "gen": "ның" if b else "нің",
        "acc": "н",
        "ins": "мен",
    }.get(case)
    return stem_poss3 + end if end else stem_poss3


def kk_rephrase_affix(placeholder: str, affix: str) -> Optional[str]:
    """Return a replacement for ``{placeholder}-affix`` that does not depend on the
    substituted value, e.g. ``{provider}-ға`` -> ``{provider} қызметіне``."""
    case = KK_AFFIX_CASE.get(affix.lower())
    if case not in ("dat", "loc", "abl", "gen", "acc", "ins"):
        return None
    name = placeholder.strip("{}").lower()
    for pat, noun, harm in HEAD_NOUNS:
        if re.search(pat, name):
            head = kk_case_after_poss3(kk_poss3(noun, harm), case, harm)
            if affix.isupper():
                head = head.upper()
            return f"{placeholder} {head}"
    return None


def kk_choose_affix(word: str, case: str) -> str:
    """Runtime-correct affix for a word (reference implementation of the rule the
    game client should apply; mirrored in docs/dart/kk_affix.dart)."""
    w = word.lower()
    vowels = [c for c in w if c in KK_VOWELS]
    back = True
    for c in reversed(vowels):
        if c in KK_FRONT_VOWELS:
            back = False; break
        if c in KK_BACK_VOWELS:
            back = True; break
    last = w[-1] if w else "а"
    voiceless = set("кқпстфхцчшщ")
    sonor = set("лмнңрйуwиy")
    if last.isdigit():
        # pronounce digits: approximate by final digit's Kazakh name
        names = {"0": "нөл", "1": "бір", "2": "екі", "3": "үш", "4": "төрт", "5": "бес",
                 "6": "алты", "7": "жеті", "8": "сегіз", "9": "тоғыз"}
        return kk_choose_affix(names[last], case)
    if case == "dat":
        if last in KK_VOWELS or last in sonor or last in "жз":
            return "ға" if back else "ге"
        return "қа" if back else "ке"
    if case == "loc":
        return ("та" if back else "те") if last in voiceless else ("да" if back else "де")
    if case == "abl":
        if last in "мнң":
            return "нан" if back else "нен"
        return ("тан" if back else "тен") if last in voiceless else ("дан" if back else "ден")
    if case == "gen":
        if last in KK_VOWELS or last in "мнң":
            return "ның" if back else "нің"
        return ("тың" if back else "тің") if last in voiceless or last in "бвгд" else ("дың" if back else "дің")
    if case == "acc":
        if last in KK_VOWELS:
            return "ны" if back else "ні"
        return ("ты" if back else "ті") if last in voiceless or last in "бвгд" else ("ды" if back else "ді")
    if case == "ins":
        if last in voiceless:
            return "пен"
        if last in "жз" or last in "бвгд":
            return "бен"
        return "мен"
    return ""


# Official Kazakh Latin alphabet (Decree of 2021, 31 letters).
KK_LATIN_2021 = {
    "а": "a", "ә": "ä", "б": "b", "в": "v", "г": "g", "ғ": "ğ", "д": "d", "е": "e", "ё": "io",
    "ж": "j", "з": "z", "и": "i", "й": "i", "к": "k", "қ": "q", "л": "l", "м": "m", "н": "n",
    "ң": "ñ", "о": "o", "ө": "ö", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ұ": "ū",
    "ү": "ü", "ф": "f", "х": "h", "һ": "h", "ц": "ts", "ч": "ch", "ш": "ş", "щ": "şş", "ъ": "",
    "ы": "y", "і": "ı", "ь": "", "э": "e", "ю": "iu", "я": "ia",
}


_LAT_UPPER = {"i": "İ", "ı": "I"}


def _lat_upper(s: str) -> str:
    return "".join(_LAT_UPPER.get(c, c.upper()) for c in s)


def kk_to_latin(text: str) -> str:
    """Transliterate using the 2021 alphabet. Case: a capital Cyrillic letter maps to a
    capitalised Latin digraph («Ш»→«Ş», «Ю»→«Iu»), or fully upper («IU») inside an all-caps word."""
    out = []
    words = re.split(r"(\W+)", text)
    for w in words:
        caps = len(w) > 1 and w.isupper()
        for ch in w:
            low = ch.lower()
            lat = KK_LATIN_2021.get(low)
            if lat is None:
                out.append(ch)
            elif ch != low:
                out.append(_lat_upper(lat) if caps else _lat_upper(lat[:1]) + lat[1:])
            else:
                out.append(lat)
    return "".join(out)


# --------------------------------------------------------------------------
# Uzbek (Latin)
# --------------------------------------------------------------------------
UZ_PROFILE = Profile(code="uz", script="latn", special_letters="", plural_categories={"one", "other"},
                     loanwords=set())
UZ_APOSTROPHES = "‘’ʻʼ'`"  # official: oʻ gʻ use U+02BB, tutuq belgisi U+02BC
UZ_MARKERS = re.compile(r"\b\w*(o[‘’ʻʼ'`]|g[‘’ʻʼ'`])\w*|\b\w+(lar|ni|ga|dan|ning|dagi|siz|ingiz|ish|moq)\b", re.I)

# Portuguese / Spanish / English markers for wrong-language detection.
PT_ES_MARKERS = re.compile(r"\b(para|por|favor|será|aplicativo|novamente|perfil|jogo|usted|está|el|los|las|de la|não|você)\b", re.I)
EN_MARKERS = re.compile(r"\b(the|you|your|and|of|to|is|are|with|for|in|on|this|that|equipped|click|tap)\b", re.I)

GENERIC_PROFILE = Profile(code="xx", script="any")

PROFILES = {"kk": KK_PROFILE, "uz": UZ_PROFILE}


def profile_for(locale: Optional[str]) -> Profile:
    if not locale:
        return GENERIC_PROFILE
    return PROFILES.get(locale.split("_")[0].split("-")[0].lower(), GENERIC_PROFILE)


def script_counts(text: str) -> Tuple[int, int]:
    """(#cyrillic letters, #latin letters) ignoring placeholders and tags."""
    clean = re.sub(r"\{[^{}]*\}|<[^>]*>|%\d*\$?[sd]|https?://\S+", "", text)
    cyr = len(re.findall(rf"[{CYR}{KK_EXTRA}{UZ_CYR_EXTRA}]", clean))
    lat = len(re.findall(r"[A-Za-zÀ-ÿĞğŞşÑñŪū]", clean))
    return cyr, lat
