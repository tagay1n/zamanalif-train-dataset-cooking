# DSL Policy Audit

DSL choices are reserved for cases where the references show competing accepted
Zamanalif outputs for the same Cyrillic input or for the same clear convention.
If PDF and ANTAT agree, the converter should emit a deterministic result instead
of a DSL choice.

## Kept as Active DSL

No Russian-sign DSL rules remain.

`E_GLIDE` remains parseable and resolvable only for legacy stored reviews. It
is not an active annotation rule and no new conversion or export emits it.

## Converted Back To Deterministic Rules

- Ordinary Russian `ь/ъ` signs are always preserved as `ʼ` in new conversions.
- Russian consonant + `я/ю/ё` always uses an explicit `y` glide in new
  conversions.
- Cyrillic `ц` always renders as plain `ts` in new conversions.
- Russian `ье` always renders as `ʼye`; Russian `ъе` always renders as `ye`.
- Russian `ьо` always renders as `ʼo`; neither `ʼyo` nor omission is active.
  Former `ьо` words use ordinary routing and may still require catchall review
  because of other conditional letters.
- Eligible Cyrillic `ие` always renders as `iye` in new conversions. This
  includes the `проект` family (`проект -> proyekt`) and native forms such as
  `тиеш -> tiyeş`. Exact surname endings `-иев`, `-иева`, `-әев`, and `-әева`
  retain their established spellings; this exception does not cover longer
  derived forms such as `Дмитриевка -> Dmitriyevka`. ANTAT `proekt` and the PDF
  `tieş` are deliberately excluded by this narrow dataset policy.
- `TS`, `RUS_SIGN`, `RUS_JOTATION`, and `RUS_SIGN_E` are retired: they are not
  registered DSL rules and old projects using them are unsupported.
- `RL_Y`: removed as DSL. Cyrillic `ы` is always `ı`; an explicit following
  `й` is required for `ıy`. Thus `сыр -> sır`, while `сыйр -> sıyr`.
- `MONTH_NAME`: removed as DSL. Month names use the ordinary converter and
  participate in the same origin and remaining-DSL routing as other words.
- `RL_FINAL_KA`: removed as DSL. Whether final Cyrillic `-ка` contains a
  Russian stem `к` or a Tatar suffix consonant is a lexical boundary decision,
  not a global spelling policy. Verified suffixed stems are converted
  deterministically; the ordinary converter supplies one suggestion for other
  forms, and unresolved forms are reviewed in catchall.
- `NATIVE_UW`: removed as DSL. The dataset follows the reference PDF's economy
  principle and does not write a perceived glide between native `u/ü` and a
  following vowel: `baruı`, `kilüe`, `bua`, `buın`, `tuu`, and `cilquar`.
  Genuine `w`, including verbal-noun forms after stems ending in `a/ä`, remains
  deterministic: `cırlaw`, `aşaw`, `söyläw`, `däwalaw`.
- `IYA`: removed as DSL. The dataset intentionally keeps the former preferred
  explicit-glide standard despite the reference PDF's compact spelling:
  `ия -> iyä`, `орфография -> orfografiyä`, and `әдәбият -> ädäbiyat`.
- `ARABIC_INITIAL_GA`: removed as DSL. The `гади` family now follows the
  preferred plain initial `га` convention (`ğadi`, `ğadiläşterergä`); ANTAT
  front variants are preserved in fixture comments.
- `GIY_COMPACT`: removed as DSL. Coherent reference cases such as
  `гыйльми -> ğilmi`, `кагыйдә -> qağidä`, and `шагыйрь -> şağir` are handled by
  deterministic lexical conventions.
- `ARABIC_FINAL_AT`: removed as DSL. Coherent reference cases such as
  `канәгать -> qanäğät`, `сәгать -> säğät`, and `җинаять -> cinayät` are handled
  by deterministic lexical conventions.
- Broad Arabic initial `га` fronting was removed. Coherent stems such as
  `гадәт`, `гаеп`, `гаскәр`, `гаять`, `гамәл`, and `гарип` are deterministic,
  while unrelated words such as `гасыр` stay `ğasır`.

## Next Audit Candidates
