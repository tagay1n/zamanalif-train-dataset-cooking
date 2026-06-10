# Zamanalif Conversion Rules for Annotators

This document summarizes the conversion rules currently used for Tatar
Cyrillic to Zamanalif annotation work. It is intended as a practical reference
for Label Studio annotators.

The automatic conversion shown in Label Studio is only a suggestion. Review it
especially carefully when the word contains conditional letters or when Gemini
marked the word as a Russian/Russian-international loanword (`RL`) or unknown
(`U`).

## Zamanalif Alphabet and Unicode

Use real Unicode Zamanalif Latin characters:

```text
a b c ç d e f g ğ h i ı j k l m n ñ o ö p q r s ş t u ü v w x y z
A B C Ç D E F G Ğ H İ I J K L M N Ñ O Ö P Q R S Ş T U Ü V W X Y Z
```

Important Unicode caveats:

- `ı` is Latin small dotless i, U+0131.
- `İ` is Latin capital I with dot, U+0130.
- `ş`, `ç`, `ğ`, `ñ`, `ä`, `ö`, `ü` must be Latin letters, not Cyrillic
  lookalikes.

## Usually Deterministic Mappings

These mappings normally do not need human review by themselves:

| Cyrillic | Zamanalif | Example |
| --- | --- | --- |
| А а | A a | азатлык -> azatlıq |
| Ә ә | Ä ä | әни -> äni |
| О о | O o | болыт -> bolıt |
| Ө ө | Ö ö | төтен -> töten |
| Ы ы | I ı | ылыс -> ılıs |
| Э э | E e | эт -> et |
| И и | İ i | китап -> kitap |
| Б б | B b | бабай -> babay |
| Җ җ | C c | җир -> cir |
| Ч ч | Ç ç | ачкыч -> açqıç |
| Д д | D d | давыл -> dawıl |
| Ф ф | F f | фонд -> fond |
| Һ һ | H h | шәһәр -> şähär |
| Ж ж | J j | журнал -> jurnal |
| Л л | L l | гөлләр -> göllär |
| М м | M m | малай -> malay |
| Н н | N n | төн -> tön |
| Ң ң | Ñ ñ | зәңгәр -> zäñgär |
| П п | P p | туп -> tup |
| Р р | R r | рәхәт -> räxät |
| С с | S s | мисал -> misal |
| Ш ш | Ş ş | буш -> buş |
| Т т | T t | тар -> tar |
| Х х | X x | хат -> xat |
| Й й | Y y | йокы -> yokı, ай -> ay |
| З з | Z z | зур -> zur |

## Conditional Letters

Conditional letters are the main annotation target:

```text
У у, Ү ү, Г г, К к, В в, Я я, Ю ю, Е е, Ц ц
```

Review words containing these letters carefully:

- `в`: native `w`, loanword `v`.
- `г`: native front-vowel `g`, native back-vowel `ğ`, loanword `g`.
- `к`: native front-vowel `k`, native back-vowel `q`, loanword `k`.
- `у`: usually `u`; final native `ау/әү` may become `aw/äw`.
- `ү`: usually `ü`, but still reviewed because it interacts with harmony and
  nearby conditional letters.
- `я`: native back-vowel `ya`, native front-vowel `yä`, after `и` may be `a`
  or `ä`, loanword usually `ya`.
- `ю`: native back-vowel `yu`, native front-vowel `yü`, after `и` is `iü`,
  loanword usually `yu`.
- `е`: initial native back-vowel `yı`, initial native front-vowel `ye`,
  internal native after consonant `e`, after `и` `e`. Loanword behavior needs
  review.
- `ц`: loanword `s` at word start/end or after consonants, `ts` after vowels.

## Russian-Loan Review Cases

These cases are especially relevant for words marked by Gemini as `RL`:

- `ё` is not normalized to `е`; in Russian/Russian-through-Russian loanwords it
  becomes `yo`: `шофёр -> şofyor`.
- Russian/Russian-through-Russian `ы` can become `ıy`: `сыр -> sıyr`,
  `вышка -> vıyşka`, `музыка -> muzıyka`. Native `ы` remains `ı`.
- `ь` and `ъ` are not Zamanalif letters. In Russian loanwords with softened
  consonants after back vowels, apostrophe may be used:
  `роль -> rol'`, `культура -> kul'tura`.
- `щ` becomes `şç`.

## Vowel Harmony and Known Caveats

The exporter uses a simple vowel-harmony signal:

- front vowels: `ә е ө ү и`;
- back vowels: `а о у ы`;
- `mixed_front_back` means the normalized word has at least one front and one
  back vowel.

Project 1 skips native-looking `N` words with mixed front/back vowel harmony,
but keeps matching `RL` and `U` words.

Important caveats:

- Final native `[u]/[ü]` words like `бу`, `су`, `үсү` are written with `u/ü`,
  not final `w`. The `w` case is for forms such as verbal nouns after stems
  ending in `а/ә`: `җырлау -> cırlaw`, `сөйләү -> söyläw`.
- `g/ğ` and `k/q` cannot be decided only by front/back vowels in every word.
  Examples such as `гармун`, `гараж`, `вагон`, `кәгазь`, `кодрәт`, and `куәт`
  are why these letters remain dictionary-review targets.
- Arabic-Persian loans include lexical exceptions, so dictionary review is
  still useful even when the automatic suggestion looks plausible.

## Label Studio Project 1 Guidance

Project 1 is word dictionary review:

- annotate one normalized word form only once;
- correct the suggested Zamanalif form when it is wrong;
- trust deterministic letters more than conditional letters;
- use Gemini origin prediction as a weak hint, not final truth;
- for `U` words, prefer careful correction over guessing aggressively.
