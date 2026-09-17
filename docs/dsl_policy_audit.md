# DSL Policy Audit

DSL choices are reserved for cases where the references show competing accepted
Zamanalif outputs for the same Cyrillic input or for the same clear convention.
If PDF and ANTAT agree, the converter should emit a deterministic result instead
of a DSL choice.

## Kept as DSL

- `E_GLIDE`: kept pending a separate audit of `ие -> ie/iye`.
- `RUS_SIGN`, `RUS_JOTATION`: kept for Russian
  sign/apostrophe policy differences.

## Converted Back To Deterministic Rules

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

- `E_GLIDE`
