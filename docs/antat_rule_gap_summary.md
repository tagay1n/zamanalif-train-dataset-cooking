# Antat Rule Gap Summary

This document summarizes the current strict Antat gold-standard coverage test.
The test extracts comparable word pairs from aligned Antat Cyrillic/Zamanalif
dictionary entries and tries both converter branches:

- `convert_for_annotation(word, "N")`
- `convert_for_annotation(word, "RL")`

A pair is a rule gap only when neither branch matches the Antat Zamanalif form.

Current coverage:

| Metric | Count |
|---|---:|
| Extracted comparable pairs | 9631 |
| Covered by native branch only | 5310 |
| Covered by loanword branch only | 507 |
| Covered by both branches | 1721 |
| Rule gaps | 2093 |

Most rule gaps contain at least one conditional or review letter. Counts by
letter across rule-gap words:

| Letter | Count |
|---|---:|
| е | 1047 |
| к | 962 |
| г | 632 |
| ы | 597 |
| я | 455 |
| у | 440 |
| ь | 225 |
| в | 193 |
| ү | 187 |
| ц | 131 |
| ю | 65 |
| ъ | 50 |
| ё | 24 |
| щ | 7 |

## Gap Categories

### `е -> yı/ye/e` Context Rules

Count: 690.

The current rules often treat internal `е` too simply. Antat has many cases
where the gold form uses `ye`, `yı`, or plain `e` depending on word structure.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| тыелырга | tıyılırğa | tıelıra | tıyyelıyrga | ABSTAIN |
| сыеныр | sıyınır | sıenır | sıyyenıyr | ACCOMMODATION |
| камилләштерергә | kamilläşterergä | amilläştererä | kamilläştyeryergä | ACCOMPLISH |

### `у/ү -> w` and Glide Rules

Count: 313.

The current native `у` rule only handles word-final `ау/әү`, but Antat shows
`w` in additional positions and compounds.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| каушатырга | qawşatırğa | qauşatırğa | kauşatıyrga | ABASH |
| эшләү | eşläw | eşläü | eşläü | ABILITY |
| куыш | quwış | quış | kuıyş | ACCOMMODATION |

### `ия` / Final `я` in Loanwords and Native Words

Count: 298.

Many words with `ия` need `iyä`, while current branches often produce `iya`,
drop `к/г` incorrectly under mixed harmony, or handle `е/я` independently.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| академия | akademiyä | aademi | akadyemiya | ACADEMY |
| абстракциягә | abstraksiyägä | abstrasiä | abstraksiyagä | ABSTRACT |
| әһәмиятле | ähämiyätle | ähämiätle | ähämiyatlye | ACCOUNT |

### Soft Sign / Apostrophe Rules

Count: 219.

The current loanword branch usually maps `ь` to apostrophe directly, but Antat
often drops it or uses an apostrophe in a more specific position.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| козырь | kozır | qozır | kozıyr' | ACE |
| мәкаль | mäqal | mäal | mäkal' | ADAGE |
| консультация | konsul’tatsiyä | onsultatsi | konsul'tatsiya | ADVICE |

### `к -> k/q` and Mixed-Harmony Branching

Count: 153.

Several gaps show that whole-word front/back harmony is not sufficient for `к`.
Loanwords and mixed-harmony words may still need `q` in suffixes or native
segments.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| климатка | klimatqa | limata | klimatka | ACCLIMATIZE |
| һәркайда | härqayda | härayda | härkayda | ABROAD |
| күралмаска | küralmasqa | üralmasa | küralmaska | ABHOR |

### Hyphen Preservation

Count: 150.

The converter currently drops hyphens because non-Cyrillic characters are not
preserved during character-by-character conversion.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| исән-сау | isän-saw | isänsaw | isänsau | ABLE-BODIED |
| әйләнә-тирәдә | äylänä-tirädä | äylänätirädä | äylänätirädä | ABOUT |
| үз-үзеңне | üz-üzeñne | üzüzeñne | üzüzyeñnye | ABSTAIN |

### `г -> g/ğ` and Mixed-Harmony Branching

Count: 97.

The current native branch returns an empty string for `г` in mixed-harmony words,
while Antat often expects `ğ`.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| гамәлдән | ğämäldän | amäldän | gamäldän | ABOLISH |
| тәмамларга | tämamlarğa | tämamlara | tämamlarga | ACCOMPLISH |
| гадәт | ğädät | adät | gadät | ACTION |

### Hard Sign / Apostrophe Rules

Count: 50.

The current handling of `ъ` as apostrophe does not match many Antat forms.
Several Arabic/Persian-origin words use `ğ` or no apostrophe in the gold form.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| мәгънәле | mäğnäle | mägnäle | mäg'nälye | ABSTRUSE |
| икърар | iqrar | irar | ik'rar | ACCEPT |
| дәгъва | däğwa | däwa | däg'va | ACTION |

### `я -> ya/yä/a/ä/iä` Rules

Count: 39.

Some `я` cases need `yä` or `ya` after consonants, while others need a form that
interacts with preceding vowels or soft signs.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| хыянәт | xıyänät | xınät | xıyyanät | ABUSE |
| киная | kinayä | ina | kinaya | ALLUSION |
| отряд | otr’ad | otryad | otryad | ADVANCE-GUARD |

### `в -> v/w` Rules

Count: 28.

Origin alone is not enough for some `в` cases. Antat has native-looking words
with `w`, loanwords with `v`, and compounds where nearby vowels affect output.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| әүвәл | äwwäl | äüwäl | äüväl | BEFORE |
| тәүдә | täwdä | täüdä | täüdä | BEFORE |
| вазифасы | wazıyfası | wazifası | vazifasıy | CHAIRMANSHIP |

### `ё` and Apostrophe Before `o`

Count: 24.

The current converter maps `ё -> yo`, but Antat often uses apostrophe plus `o`
for Russian loanwords.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| счёт | sç’ot | sçyot | sçyot | ACCOUNT |
| самолёт | samol’ot | samolyot | samolyot | AEROPLANE |
| паникёр | panik’or | paniyor | panikyor | ALARMIST |

### `ю -> yu/yü/u/ü` Rules

Count: 16.

Some `ю` cases require `yu`, some require apostrophe behavior in loanwords, and
some extracted examples contain phrase-level alignment noise.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| юк | yuq | yu | yuk | ABOLISH |
| брошюра | broş’ura | broşyura | broşyura | BROCHURE |
| бюро | b’uro | byuro | byuro | BUREAU |

### Other / Extraction Noise or Missing Rule

Count: 13.

These need manual review first. Some look like extraction artifacts from
dictionary typography, while others are real apostrophe/Arabic-origin rules.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| лд | xəldə | ld | ld | ABASH |
| тәэсир | tä’sir | täesir | täesir | ACT |
| тәэмин | tä’min | täemin | täemin | AMMUNITION |

### `ы -> ı/iy` Rules

Count: 2.

Only two gaps were classified here directly. Most `ы` gaps are mixed with other
conditional-letter issues.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| җәфаланып | cafalanıp | cäfalanıp | cäfalanıyp | CARE-WORN |
| җәфаландыру | cafalandıru | cäfalandıru | cäfalandıyru | INFLICTION |

### `ц -> ts/s` Rules

Count: 1.

Only one direct `ц` gap was classified. Most `ц` mismatches are part of `ия`
or soft-sign loanword patterns.

| Cyrillic | Antat | Native output | Loanword output | Headword |
|---|---|---|---|---|
| винтсыман | vintsıman | wintsıman | vintsıyman | CORK-SCREW |

## Notes

- The examples above are generated from the current extractor, so a few entries
  may be extraction noise rather than converter-rule bugs.
- A rule gap means neither the native nor loanword branch currently produces the
  Antat gold form.
- Origin annotation is still needed later for branch choice, but these gaps are
  cases where branch choice alone is insufficient.
