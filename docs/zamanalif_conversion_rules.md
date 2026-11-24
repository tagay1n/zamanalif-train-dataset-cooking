# Zamanalif Conversion Rules for Annotators

This document summarizes the conversion rules currently used for Tatar
Cyrillic to Zamanalif annotation work. It is intended as a practical reference
for Label Studio annotators.

The automatic conversion shown in Label Studio is only a suggestion. Review it
especially carefully when the word contains conditional letters or when Gemini
marked the word as a Russian/Russian-international loanword (`RL`) or unknown
(`U`).

## Conversion Decision Types

The pipeline distinguishes four cases:

- **deterministic conversion**: one mechanical Zamanalif output;
- **convention choice**: multiple accepted writing policies, represented by DSL;
- **lexical choice**: one correct result for a word after origin and identity are
  known, resolved by Project 1 review;
- **contextual homonym**: conversion depends on sentence meaning, deferred to
  Project 2.

Converter uncertainty is not an accepted variant. It must remain a review item.

The dataset intentionally writes an explicit `y` in Cyrillic `ия`, despite the
reference PDF's compact convention: `ия -> iyä`, `орфография -> orfografiyä`,
and `әдәбият -> ädäbiyat`. This is deterministic and is not represented by DSL.

For new conversions, eligible Cyrillic `ие` is likewise deterministic: it is
written `iye`, including `проект -> proyekt`, `тиеш -> tiyeş`, and
`Дмитриевка -> Dmitriyevka`. The exact surname endings `-иев`, `-иева`,
`-әев`, and `-әева` keep their established spelling (for example,
`Нуриев -> Nuriev`); the exception does not extend to longer derived forms.
This deliberately differs from ANTAT's `proekt` and the PDF's `tieş`.
`E_GLIDE` is only retained to read and resolve legacy stored reviews; it is not
an active DSL choice or Label Studio project for new annotation tasks.

Rule and option names are stable API identifiers. DSL choices cover only the
substring that differs.

DSL is never part of the model-training target. The final training exporter
resolves every choice through a registered default or explicit CLI policy and
rejects output in which DSL delimiters remain.

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

Final training-target validation checks Unicode Cyrillic letters and Cyrillic
combining marks beyond the Tatar alphabet, including `Қ`, `І`, `ғ`, and `ѵ`.
Any remaining such character blocks export until its conversion is reviewed;
validation does not replace it or change the source. The existing standalone
`ъ`/`ь` letter-reference exceptions remain allowed. Valid Zamanalif letters
and Greek/math notation retain their existing handling.

## Roman Numeral Literals in Sentence Export

Canonical multi-character Roman numerals containing Ukrainian `І` (U+0406),
or Cyrillic `Х` (U+0425) mixed with Latin Roman letters, use ASCII Roman
letters in training targets and corpus projections: `ХІХ -> XIX`,
`ХХІ -> XXI`, `ІІ -> II`, and `ХХIХ -> XXIX`. Roman `I` stays ASCII `I`,
distinct from the phonetic conversion of Tatar `И` to `İ`.

Isolated Ukrainian `І` used as a Roman or scientific literal likewise becomes
ASCII `I`, and the historical fractional notation `ІІ1/2` becomes `II1/2`.
Initial-like `І.` forms and foreign letters embedded in ordinary words remain
unchanged pending separate review. Ordinary Tatar `И -> İ` is unaffected.

This is a derived conversion view. Stored source sentences, annotation tokens,
origins and reviews remain intact. Numerals are recognized across annotation
boundaries, including unannotated literal gaps. Quotes, spacing and punctuation
are preserved. Other single-character numerals, pure Cyrillic `Х`-only sequences,
malformed Roman spellings, ordinary words and name initials keep their existing
handling.

## Celsius Unit Literals in Sentence Export

The Celsius unit `°С` containing Cyrillic capital `С` becomes Latin `°C`
in training targets and corpus projections. Existing Latin `°C` stays intact.
Both source spellings remain as written; numbers, whitespace, annotation tokens,
origins and reviews are preserved. This rule works across annotation boundaries
and unannotated literal gaps, like the Roman numeral projection above.

Ordinary Tatar `С -> S` is unaffected. Geographic abbreviations `С.Ш.`/`С.ш.`,
embedded letters and lowercase `с` do not qualify for this unit rule.

## Model Identifier Casing

The reviewed voltmeter model identifier `Ц20` is rendered `Ts20` in sentence
exports and corpus views. This is a casing convention for that exact identifier;
its digits, surrounding punctuation and stored source remain intact. Existing
case handling for other identifiers and all-capital words remains in effect.

## Reviewed Source-Preserving Sentence Repairs

The Project 59 surname reviews use `RL` for `галимовалардан -> ğalimovalardan`,
`галимовка -> ğalimovqa`, and `галимҗанованың -> ğalimcanovanıñ`, following the
reviewed surname forms `галимова`, `галимов`, and `галимҗанова`. The spelling
validator accepts these exact pairs with their Tatar endings.

The reviewed `шәркыятьче -> şärqıyätçe` stem also appears in Project 59 as
`шәркыять -> şärqıyät`, `шәркыятьчесе -> şärqıyätçese`, and
`галим-шәркыятче -> ğalim-şärqıyätçe`. The spelling validator accepts these
four exact pairs.

Literal dictionary spellings can retain an en dash `–` from the source. For
example, `иҗтимагый–сәяси -> ictimağıy–säyäsi` preserves the written separator.

Soft hyphens (`U+00AD`) between letters represent discretionary line breaks.
Training export's punctuation check accepts their omission in Zamanalif, as in
`дөнья\u00ADдан -> dönyadan`, while preserving the exact Cyrillic source.

Dotted initials joined to a name retain source capitalization and periods in
training export. Examples include `Ф.Ә.Ганиева -> F.Ä.Ğaniyeva`,
`К.Газим -> Q.Ğazim`, and `Х.Халиковның -> X.Xalikovnıñ`.

The reviewed `Ф.Г.Галимуллин -> F.Ğ.Ğalimullin` follows the existing
spellings of `Галимулла` and `Галимуллин`. Four reviewed company abbreviations
`ГАҖ` use `ĞAC` in their specific contexts. The inflected `ГАҖнең` uses
`ĞACneñ`, retaining the original uppercase stem and lowercase ending.

The joined source tokens `галәмин.Уәссаләтү` and `әҗмәгыйн.Бөтен` retain
their periods and following capitals as `ğalämin.Uässalätü` and
`äcmäğıyn.Böten`.

The reviewed joined forms retain their original boundaries and capital runs.
Training export renders `июнендәгеОАО -> iyünendägeOAO`,
`мөмкинГади -> mömkinĞadi`, `МӨХӘММӘДИЕВлар -> MÖXÄMMÄDİYEVlar`,
and `#ФәритЯхинныукыйбыз -> #FäritYäxinnıuqıybız`.

The spelling validator recognizes the reviewed stem `җинаять -> cinayät` and
the written variants `жинаять -> jinayät` and `җиңаять -> ciñayät`. The rest of
each word still follows the ordinary letter checks, including written endings.

The reviewed `җәмгыят -> cämğıyät` stem keeps dotless `ı` and `yä`. The spelling
validator also recognizes `жәмгыят -> jämğıyät`, `жҗәмгыят -> jcämğıyät`, and
the joined `хуҗалыкҗәмгыят -> xucalıqcämğıyät` stem. Each form retains the
consonants and endings written in the source.

Project 59 retains the reviewed second `q` and written `ә` in
`хәкыйкәт -> xäqıyqät` and `хәкыйкәтнең -> xäqıyqätneñ`. The spelling
validator accepts these two exact pairs.

The spelling validator recognizes the reviewed stem `риваят -> riwayät`,
including `риваяте -> riwayäte` and `риваятендә -> riwayätendä`. It checks
each ending against the corresponding source letters.

The spelling validator recognizes reviewed `кыяфәт -> qıyäfät`, including
`калып-кыяфәт` compounds, `шикаят -> şiqayät`, and the two written stems
`тәрәккыят` and `тәрәкъкыят` with `täräqqıyät`. It checks their endings against
the corresponding source letters.

The Project 59 number compounds retain the separate `yıl` and `yul`
components, for example `икееллыгы -> ikeyıllığı`,
`йөзъеллыкның -> yözyıllıqnıñ`, and `икеюллыкны -> ikeyullıqnı`. The spelling
validator accepts the eight exact reviews, including `жедееллыкта -> jedeyıllıqta`
with its written initial consonant.

Sixteen Project 59 surname reviews explicitly use `iye` in `-иев` or `-иева`,
for example `шагалиев -> şağaliyev` and `гарәфиева -> ğaräfiyeva`. The spelling
validator accepts the exact reviewed pairs. The default surname convention
still supplies the initial annotation suggestions.

The spelling validator also accepts the reviewed `eye` in
`галеев -> ğaleyev`, `камбеева -> qambeyeva`, `кыдыкеева -> qıdıkeyeva`, and
`миргалеев -> mirğaleyev`.

Project 59 reviews retain surname-final `v` before the Tatar dative
`-ка -> -qa`, as in `искәндәровка -> iskändärovqa` and
`шәймиевка -> şäymiyevqa`. The spelling validator accepts the exact
reviewed pairs. It also accepts reviewed `v` before other Tatar endings,
as in `галиевне -> ğaliyevne` and `гариповларга -> ğaripovlarğa`.

The spelling validator accepts the 17 exact Project 59 reviews with
`-ayev` or `-ıyev`, including `Гыймаев -> Ğıymayev`,
`Капкаевның -> Qapqayevnıñ`, and `Сәгыевләр -> Säğıyevlär`.

Tatar contexts referring to the poet use `Тукаев -> Tuqayev` and
`Тукаевның -> Tuqayevnıñ` through contextual reviews. Other reviewed people
sharing the surname use `Tukayev` in their bare-surname occurrences. The Russian
quotation `Габдуллы Тукая` uses `Tukaya` for `Тукая`. The three Project 59
submissions retain `tuqayevağa`, `tuqayevqa`, and `tuqayevlarnıñ`.

The reviewed stem `казыят -> qazıyät` is used in both the singular and forms
with endings, including `казыятләр -> qazıyätlär`.

The reviewed `әүвәл -> äüwäl` spelling also appears in both forms of the
same month, `рабигыль-әүвәл -> rabiğıl-äüwäl` and
`рабигыльәүвәл -> rabiğıläüwäl`. The spelling validator accepts all three
exact pairs, with each compound retaining its written separator layout.

The reviewed `тәүфикъ` and `тәүфыйк` stems use `täüf-`, as in `täüfiq`,
`täüfıyq`, `täüfiqqa`, and `täüfıyqsızlıqta`. Each form keeps the written
distinction between `и` and `ый` and its own ending.

The two written forms `фәүкылгадә` and `фәүкыльгадә` use the reviewed
spelling `fäüqılğadä`. The validator accepts that exact spelling for each.

The reviewed forms `тәсаувыр -> täsauwır` and `тәсаувыф -> täsauwıf`
render written `ув` as `uw`. The separate form `тәсаввыф -> täsawwıf`
renders written `вв` as `ww`.

In the two reviewed mixed-script forms `мəгарифен -> mäğarifen` and
`мөстəкыйль -> möstäqıyl`, Latin `ə` renders as `ä`, matching the reviewed
Cyrillic equivalents. The validator accepts each exact pair.

The reviewed `iğtibar` stem also appears with a soft sign or with neither
sign in `игьтибар -> iğtibar`, `игьтибарны -> iğtibarnı`, and
`игтибарлыкларын -> iğtibarlıqların`. The validator accepts those exact pairs.

The reviewed `зачет-ларга -> zaçyot-larğa` reflects Russian `зачёт` in an
academic context. The validator accepts this exact pair with the written hyphen.

The reviewed written forms `диккатъ` and `диккать` both use `diqqat`,
matching the earlier `дикъкать -> diqqat` review. The validator accepts both
exact pairs.

The reviewed forms `еглавы -> yığlawı` and `еглаучыларны -> yığlawçılarnı`
use the `yığla-` stem from `еглаган -> yığlağan`. The validator accepts both
exact pairs.

The five reviewed `Жаек` and `Җаек` family forms retain `q` in the stem.
`Жаек -> Jayıq`, `Жаек-Идел -> Jayıq-İdel`, and `Җаектагы -> Cayıqtağı`
follow the earlier reviews `Җаек -> Cayıq` and `Жаекта -> Jayıqta`.
The dative forms are `Жаекка -> Jayıqqa` and `Җаекка -> Cayıqqa`.

The Project 59 submission `фидакярлекләр -> fidaqärleklär` uses `fidaqär-`.
The validator accepts this exact pair.

The reviewed compound `шагыйрь-шагыйрәләр -> şağiyr-şağıyrälär` retains the
submitted spelling and the written hyphen. The validator accepts this exact pair.

The reviewed form `хосусыятьләре -> xosusıyätläre` uses `-ıyät-`, as in
`җәмгыять -> cämğıyät`. The validator accepts this exact pair.

The reviewed forms `жөнгыятьне -> jönğıyätne`,
`муаффәкыятьсез -> muaffäqıyätsez`, and `табигыят -> tabiğıyät` also
use `-ıyät-`. The validator accepts those exact pairs.

The Project 59 submissions `кярханәләрен -> qärxanälären` and
`сәнгатькяр -> sänğatqär` retain `qär`. The earlier review of
`сәнгатькярләребез` uses `sänğatkärlärebez`, following stored forms with
`сәнгатькәр-`. The validator accepts these three exact pairs.

The reviewed names `Гаптерәүф -> Ğapteräüf` and `Гауни -> Ğauni` retain
`äü` and `au`, respectively. The validator accepts those exact pairs.

The reviews `калямне -> qalämne`, `Тәгаля -> Täğalä`, and
`Әбел-Галя -> Äbel-Ğalä` use `lä` for written `ля`. The validator accepts
these exact pairs. The earlier `кәлям` review uses `käläm`, matching
`кәляме -> käläme`.

The reviewed forms `Мүгькыйл -> Müğqıyl`, `мәгьсуд -> mäğsud`, and
`Рафигь -> Rafiğ` use `ğ` for written `гь`. The validator accepts these exact
pairs.

The reviewed forms `кагидәләрен -> qağidälären` and
`кагидәсен -> qağidäsen` share the `qağidä-` stem with written `и`.
The validator accepts both exact pairs.

The reviewed forms `Ниязкол -> Niyazqol`, `Ниязколый -> Niyazqolıy`, and
`Ниязкулидан -> Niyazqulidan` share `Niyaz-` and preserve the written
`-кол` and `-кули` distinction. The validator accepts those exact pairs.

The reviewed forms `саеккан -> sayıqqan` and
`Әгъгләмовның -> Äğğlämovnıñ` retain the doubled consonants. The validator
accepts both exact pairs.

The joined forms `Эсләкавыл -> Esläkawıl` and `Үрнәкавыл -> Ürnäkawıl`
retain the final `k` of the village names and the written join before `awıl`.
The validator accepts both exact pairs.

The exact compound reviews `кирәкяраклары -> kiräkyaraqları` and
`истәлек-ядкаремне -> istälek-yädqaremne` follow the written word boundaries.

The reviewed forms `Сергачка -> Sergaçqa` and `хокуки -> xoquqi` preserve
the stems `Sergaç` and `xoquq` and their written endings. The validator
accepts both exact pairs.

The reviewed `Илялова -> İlälova` and `мөляеманә -> möläyemanä` forms
also use `lä` for written `ля`. The validator accepts both exact pairs.

The reviewed names `Мәрьям -> Märyäm` and `Мәрьямбану -> Märyämbanu` share
`Märyäm-`, as in `Мәрьямбикә`. The reviewed `Мәүликов -> Mäwlikov` matches
the Latin spelling of `Мәвликов`. The validator accepts these exact pairs.

The reviewed surnames `Галимзянов -> Ğalimzyanov` and
`Иштиряков -> İştiräkov` retain the reviewed `-zyanov` ending and the `k`
of the earlier `Иштирәков` forms, respectively. The validator accepts both
exact pairs.

The reviewed sound imitation `мияу-мияу -> miyaw-miyaw` uses `aw` in both
parts. The validator accepts this exact compound.

The validator accepts `Гатауллинның -> Ğataullinnıñ`,
`Гафиятуллиннан -> Ğafiyatullinnan`, `Корганбековның -> Qorğanbekovnıñ`,
and `Доерфер -> Doerfer` as exact surname reviews.

The exact reviews `кадаʻүк -> qadaʼük`, `сәг.кадәр -> säğ.qadär`, and
`сәнгатьҝә -> sänğatkä` preserve the original source forms and use the
standard hamza mark, written dot, and contextual `k`, respectively.

The validator accepts `дәгваларын -> däğwaların`,
`мөстәкыйльлегебезне -> möstäqıyllegebezne`, and `тирәякта -> tiräyaqta`.
Each follows earlier reviewed spellings and preserves the written endings
and boundaries.

The exact source token `Қәгазь` renders as `Käğaz` through an offset-preserving
projection to `Кәгазь` and its existing dictionary review. The stored source
spelling is retained. The source and preannotation in `sent_024416` and
`sent_005485` use the corrected `Җыелышта` and `җыелышы` and their existing
reviews `Cıyılışta` and `cıyılışı`.

The source and preannotation in `sent_018782` and `sent_024206` use
`күрсәтелә -> kürsätelä` and `меңьеллыкка -> meñyıllıqqa`, respectively.
The latter follows the reviewed stem `меңьеллык -> meñyıllıq`.

The related place names `Карамас-Пельга -> Qaramas-Pelʼga` and
`Баржы-Пельга -> Barjı-Pelʼga` both retain the written soft sign with `ʼ`.
The validator accepts both exact pairs.

The joined place name `Кошкиавыл -> Koşkiawıl` retains the reviewed component
spellings `Koşki` and `awıl` and the written boundary. In `sent_022606`,
the repaired `Корбангалиев` uses the existing `Korbanğaliev` review, and
the repaired `Курмаев` uses the occurrence spelling `Kurmayev`.

The full invocation `Әгууҙү -> Äğuuzü` in `sent_016121` keeps its written
source and is annotated as one token. The exact review permits `ҙ -> z`
in this spelling. In `sent_029514`, the clipped opening `к-көнбатыш` is
repaired to `көнбатыш -> könbatış` in both the source and token list.

The sample count label `с-алынган -> c-alınğan` treats `с` as a variable
in this exact spelling. The semiconductor passages in `sent_000592` and
`sent_014526` use Latin `n` and `p` in the source and token lists so training
output keeps the distinction in `n-tip`, `p-tip`, `n-tiptağı` and `p-tiptağı`.

The final Project 59 source repairs replace `узоклегендә` with
`үзәнлегендә -> üzänlegendä` in `sent_008331` and `исәбенка` with
`исәбеннән -> isäbennän` in `sent_025196`. The corresponding tokens are
repaired in place and use the ordinary conversion rules. The crop following
the `750 центнер` figure in `sent_025196` is not reconstructed.

The reviewed words `гаярь -> ğayär` and `гаять -> ğayät` start with `ğay-`,
as do the Project 59 forms `гаярьләренең -> ğayärläreneñ` and
`гаятьтән -> ğayättän`. The separate written forms `гаяр` and `гаят` keep their
reviewed `ğayar` and `ğayät` spellings.

The November 22, 2026 sentence audit keeps stored source text intact. Missing
annotation coverage is restored at the original offsets, and combined tokens
are split to retain source spaces and hyphens in the Latin output. Existing
origin labels are retained; contextual reviews move with their tokens without
changing their spelling, origin or timestamp.

The reviewed base word is `ярдәм -> yärdäm`, consistent with
`ярдәмендә -> yärdämendä`. This supersedes the older stored `yardäm` review.

In the voltmeter instruction `sent_001132`, the two voltage abbreviations
`3–5 В` and `6 В` use contextual Latin `3–5 V` and `6 V`. The mixed-script
source word `плюcлы`, containing Latin `c`, has the contextual spelling
`plyuslı`, following `плюс -> plyus`. The source word and its `RL` label remain
unchanged; this does not establish a general replacement for Latin `c`.

The source typo `жҗиңүче` in `sent_019555` is retained and rendered `jciñüçe`,
including the initial `ж -> j`. Deferred abbreviation, initial and list-marker
groups remain pending separate decisions.

Live annotations, backups, Label Studio files and per-group audit reports remain
in the ignored local data directories. Git records the converter changes,
regression tests and reviewed rules; it excludes credentials and SQLite files.

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

Conditional letters are a broad, inexpensive sentence-selection prefilter:

```text
У у, Ү ү, Г г, К к, В в, Я я, Ю ю, Е е, Ц ц
```

Words containing these letters deserve converter analysis:

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
  internal native after consonant `e`, after eligible `и` `ye` (so `ие -> iye`). Loanword behavior needs
  review.
- `ц`: defaults to `ts`, at every position and in every origin branch; a
  consecutive `цц` pair collapses to one `ts` (for example, `цирк -> tsirk`,
  `позиция -> pozitsiyä`, and `пицца -> pitsa`). Annotators may
  edit a catch-all suggestion to `s` only for a verified lexical exception.
- In Russian-origin words, ordinary `ь/ъ` becomes `ʼ`, consonant + `я/ю/ё`
  uses explicit `y`, `ье` becomes `ʼye`, `ъе` becomes `ye`, and `ьо` becomes
  `ʼo`. Russian-sign conversions are deterministic: `ʼyo` and omission are
  not alternatives.

This list does not determine Project 1 export. The word exporter computes both
the native and loanword conversion branches. A form needs dictionary review
only when those canonical DSL outputs differ or one branch is unavailable.
Identical branches are safe regardless of whether Gemini predicted `N`, `RL`,
or `U`.

## Deterministic Hamza Preservation

The converter preserves verified hamza positions as `ʼ` (U+02BC) in the
lexical families `иэтиляф`, `маэмай`, `таэмин`/`тәэмин`, `тәэсир`, `мөэмин`,
`мәсьәлә`, `җөрьәт`, and `коръән`, including suffix forms and individual
hyphenated components. Native examples are `iʼtiläf`, `maʼmay`, `täʼmin`,
`täʼsirendä`, `möʼmin`, `mäsʼälä`, `cörʼät`, and `qorʼän`.

Both origin branches preserve hamza, although other letters can still depend
on origin (`коръән`: native `qorʼän`, loanword `korʼän`). Unknown-origin
annotation suggestions use those same branches. Hamza itself produces plain
text, never a DSL choice or a focused annotation project. The retired `HAMZA`
rule remains unsupported. This is a lexical allowlist; arbitrary Cyrillic
`э`, `ь`, or `ъ` does not establish a hamza position. Existing approved reviews
continue to take precedence over automatic conversion in training export.

The reviewed source variant `корьән` also retains the hamza in `qorʼän`,
`корьәнгә -> qorʼängä`, and `корьәндә -> qorʼändä`. The spelling validator
accepts these three exact pairs.

## Russian-Loan Review Cases

These cases are especially relevant for words marked by Gemini as `RL`:

- `ё` is not normalized to `е`; in Russian/Russian-through-Russian loanwords it
  becomes `yo`: `шофёр -> şofyor`.
- Cyrillic `ы` is always `ı`, including in Russian and Russian-through-Russian
  loanwords: `сыр -> sır`, `вышка -> vışka`, `музыка -> muzıka`. A written
  `ый` sequence remains `ıy`, as in `сыйр -> sıyr`.
- `ь` and `ъ` are not Zamanalif letters. In Russian loanwords with softened
  consonants after back vowels, apostrophe may be used. Use the Turkic-style
  modifier letter apostrophe `ʼ` (U+02BC), not ASCII `'` or curly `’`:
  `роль -> rolʼ`, `культура -> kulʼtura`.
- `щ` becomes `şç`.

## Vowel Harmony and Known Caveats

The exporter uses a simple vowel-harmony signal:

- front vowels: `ә е ө ү и`;
- back vowels: `а о у ы`;
- `mixed_front_back` means the normalized word has at least one front and one
  back vowel.

Project 1 routes native-labelled `N` mixed front/back vowel forms to catchall
when their branches differ or either branch is unavailable. Catchall shows a
preferred plain spelling even when the underlying suggestion has convention
choices. Forms whose branches agree convert automatically. Other unreviewed
forms remain blocked from training export until an approved spelling is stored.

Hyphenated components are converted separately, preventing vowel and lexical
context from leaking across the hyphen. Initial `е` uses nearby vowel context
when the remaining vowels are mixed. If a conditional letter still cannot be
resolved, the entire suggestion is unavailable: a letter is never silently
removed to produce a partial Latin spelling. Catchall allows a blank suggestion
and requires the annotator to enter a complete spelling.

Important caveats:

- Final native `[u]/[ü]` words like `бу`, `су`, `үсү` are written with `u/ü`,
  not final `w`. The `w` case is for forms such as verbal nouns after stems
  ending in `а/ә`: `җырлау -> cırlaw`, `сөйләү -> söyläw`.
- A perceived glide before a following vowel is likewise omitted under the
  reference PDF's economy principle: `баруы -> baruı`, `килүе -> kilüe`,
  `буа -> bua`, and `буын -> buın`.
- `g/ğ` and `k/q` cannot be decided only by front/back vowels in every word.
  Examples such as `гармун`, `гараж`, `вагон`, `кәгазь`, `кодрәт`, and `куәт`
  are why these letters remain dictionary-review targets.
- Arabic-Persian loans include lexical exceptions, so dictionary review is
  still useful even when the automatic suggestion looks plausible.
- Native mixed front/back words bypass lexical replacements that change their
  Latin vowel sequence: `гарәп -> ğaräp`, `сәгать -> säğat`,
  `җәмгыяте -> cämğıyate`. Contextual letter rules, verified hamza spellings,
  consonant corrections, surname conventions, and explicit review choices remain.
  This policy applies per hyphen component. Existing human reviews take precedence.

## Deliberately Excluded PDF Policies

The target dataset will not use deliberate vowel-harmony restoration for words
that are disharmonic in Cyrillic, the PDF's rewritten month names, or phonetic
lengthening of Cyrillic `ы` to `ıy`. These are not registered DSL alternatives.

The generated PDF fixture still contains examples from both groups for audit
purposes. They must not be interpreted as the preferred dataset policy during
this transition.

## Reference Status

- The PDF is a rule reference, but some policies are intentionally excluded.
- ANTAT is evidence for another convention profile, not universal gold truth.
- A reference mismatch should become a reviewed rule, a documented exclusion,
  or an annotation decision. It should not automatically become a whole-word
  converter override.
- Exact-word converter overrides are intentionally avoided. New converter rules
  should prefer general context rules or reviewed stems; approved word-level
  results belong in the SQLite reviewed dictionary.

## Label Studio Project 1 Guidance

Project 1 is word dictionary review:

- annotate one normalized word form only once;
- review every native/loanword branch disagreement, without a frequency cutoff;
- never re-export a form already stored in `reviewed_words`;
- correct the suggested Zamanalif form when it is wrong;
- trust deterministic letters more than conditional letters;
- retain the exported Gemini origin prediction; dictionary annotators review
  only the proposed spelling or mark the word as a contextual homonym;
- for `U` words, prefer careful correction over guessing aggressively.
