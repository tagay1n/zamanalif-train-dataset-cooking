# DSL Policy Audit

DSL choices are reserved for cases where the references show competing accepted
Zamanalif outputs for the same Cyrillic input or for the same clear convention.
If PDF and ANTAT agree, the converter should emit a deterministic result instead
of a DSL choice.

## Kept as DSL

- `IYA`: compact `iä` and explicit `iyä` are both supported by project policy.
- `ARABIC_INITIAL_GA`: kept only for the `гади` family where references disagree
  (`гади -> ğadi` in the PDF fixture, `гади -> ğädi` in ANTAT; ANTAT also has
  both variants for `гадиләштерергә`).
- `IE_GLIDE`: kept pending a separate audit of `ие -> ie/iye`.
- `RUS_SIGN_GLIDE`, `RUS_SOFT_SIGN`, `RUS_JOTATED_SOFTENING`: kept for Russian
  sign/apostrophe policy differences.
- `RL_FINAL_KA`: kept pending a separate morphology audit.
- `NATIVE_UW`: kept pending a separate `u/ü + vowel` glide audit. For
  `җилкуар`-style stems, the base is normalized to `q` first, then the same
  policy is reused: `cilqu{{NATIVE_UW|plain=|glide=w}}ar`.

## Converted Back To Deterministic Rules

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

- `IE_GLIDE`
- `RL_FINAL_KA`
- `NATIVE_UW`
