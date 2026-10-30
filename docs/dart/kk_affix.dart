// Runtime Kazakh affix selection for strings where a placeholder needs a case ending.
// Mirrors locqa.langs.kk_choose_affix (tested in tests/test_locqa.py).
//
// Usage in ARB:   "giftTo": "{name}{nameDat} сыйлау"   // or rephrase with a head noun
// Usage in Dart:  l10n.giftTo(name, kkAffix(name, KkCase.dative))
enum KkCase { dative, locative, ablative, genitive, accusative, instrumental }

const _back = 'аоұыя';
const _front = 'әөүіеэ';
const _vowels = 'аоұыяәөүіеэуию';
const _voiceless = 'кқпстфхцчшщ';
const _digitNames = ['нөл', 'бір', 'екі', 'үш', 'төрт', 'бес', 'алты', 'жеті', 'сегіз', 'тоғыз'];

String kkAffix(String word, KkCase c) {
  var w = word.trim().toLowerCase();
  if (w.isEmpty) return '';
  final lastCh = w[w.length - 1];
  final d = int.tryParse(lastCh);
  if (d != null) w = _digitNames[d];
  var back = true;
  for (var i = w.length - 1; i >= 0; i--) {
    if (_front.contains(w[i])) { back = false; break; }
    if (_back.contains(w[i])) { back = true; break; }
  }
  final last = w[w.length - 1];
  final isVowel = _vowels.contains(last);
  final isVoiceless = _voiceless.contains(last);
  String p(String b, String f) => back ? b : f;
  switch (c) {
    case KkCase.dative:
      return (isVowel || 'лмнңрйжз'.contains(last)) ? p('ға', 'ге') : p('қа', 'ке');
    case KkCase.locative:
      return isVoiceless ? p('та', 'те') : p('да', 'де');
    case KkCase.ablative:
      if ('мнң'.contains(last)) return p('нан', 'нен');
      return isVoiceless ? p('тан', 'тен') : p('дан', 'ден');
    case KkCase.genitive:
      if (isVowel || 'мнң'.contains(last)) return p('ның', 'нің');
      return (isVoiceless || 'бвгд'.contains(last)) ? p('тың', 'тің') : p('дың', 'дің');
    case KkCase.accusative:
      if (isVowel) return p('ны', 'ні');
      return (isVoiceless || 'бвгд'.contains(last)) ? p('ты', 'ті') : p('ды', 'ді');
    case KkCase.instrumental:
      if (isVoiceless) return 'пен';
      if ('жзбвгд'.contains(last)) return 'бен';
      return 'мен';
  }
}
