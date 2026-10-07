# Mixed-harmony forms recovered from human-reviewed families

Recovery date: 2026-10-07. Database: `data/zamanalif.sqlite`.

Committed review timestamp: `2026-10-07T10:58:50.308253+00:00`.

Of the 8,823 unreviewed origin-dependent native-labelled mixed-harmony forms,
**111 forms across 36 families** now inherit existing direct human reviews.
They occur 1,238 times in the annotated corpus. **8,712 forms remain excluded**
from dictionary queues and training export. The dictionary queue remains empty.

The recovery inserted 111 `reviewed_words` rows, 111 `reviewed_word_derivations`
rows, and one `reviewed_word_variants` row. Origins come from the human-reviewed
source: 93 `N`, 18 `RL`. All pre-existing review rows, including timestamps and
variants, were retained unchanged. Origin-only resolutions, contextual reviews,
and Gemini preannotations were not changed.

Each derivation stores its direct human-reviewed source word, lemma, part of
speech, pinned analyzer revision, and recovery timestamp. The approved source
spelling and transferable variants determine each recovered spelling; 19 forms
retain human corrections to the automatic suggestion. New approvals use the
ordinary reviewed-word precedence during training conversion.

Safety checks used the existing family rules: unambiguous lemma and part of
speech, literal full-lemma prefixes, deterministic candidate-only divergence,
compatible conversion policies, transferable shared-prefix edits and variants,
and no conflicting direct human origins or transferred results. The mixed-harmony
queue exclusion remains unchanged for the unrecovered forms.

Analyzer revision: `18fe9e45d5672d6f6113291197449e7522df1b3e`.

Full pre-recovery SQLite backup: `data/zamanalif_before_mixed_harmony_family_20261007T105749222792Z.sqlite` (local, ignored by Git).

Recovery manifest: `data/mixed_harmony_family_recovery_20261007T105850308253Z.json` (local, ignored by Git). It records all
inserted rows, source reviews and variants, and the checks performed.

Post-commit verification checked every recovered form through the training
converter and every remaining excluded form through its rejection path.
All 47,259 stored word reviews and 479 stored variants pass dictionary validation.
SQLite integrity and foreign-key checks pass.

## Recovered forms

| Word form | Human-reviewed source | Source spelling | Recovered spelling | Origin | Lemma / POS |
| --- | --- | --- | --- | --- | --- |
| акланмадымыни | аклады | `aqladı` | `aqlanmadımıni` | N | акла / v |
| аятьләр | аять | `ayat` | `ayatlär` | N | аять / n |
| аятьтә | аять | `ayat` | `ayattä` | N | аять / n |
| баета | баетты | `bayıttı` | `bayıta` | N | бает / v |
| баеталар | баетты | `bayıttı` | `bayıtalar` | N | бает / v |
| баеттылар | баетты | `bayıttı` | `bayıttılar` | N | бает / v |
| баетыла | баетты | `bayıttı` | `bayıtıla` | N | бает / v |
| баетылды | баетты | `bayıttı` | `bayıtıldı` | N | бает / v |
| баетылып | баетты | `bayıttı` | `bayıtılıp` | N | бает / v |
| баетып | баетты | `bayıttı` | `bayıtıp` | N | бает / v |
| баетырсыз | баетты | `bayıttı` | `bayıtırsız` | N | бает / v |
| бәяннан | бәян | `bäyän` | `bäyännan` | N | бәян / n |
| бәяннар | бәян | `bäyän` | `bäyännar` | N | бәян / n |
| бәяннарда | бәян | `bäyän` | `bäyännarda` | N | бәян / n |
| бәяннарында | бәян | `bäyän` | `bäyännarında` | N | бәян / n |
| бәянны | бәян | `bäyän` | `bäyännı` | N | бәян / n |
| бәяны | бәян | `bäyän` | `bäyänı` | N | бәян / n |
| бәянына | бәян | `bäyän` | `bäyänına` | N | бәян / n |
| бәянында | бәян | `bäyän` | `bäyänında` | N | бәян / n |
| бөтендөньяда | бөтендөнья | `bötendönya` | `bötendönyada` | N | бөтендөнья / adj |
| бөтендөньяның | бөтендөнья | `bötendönya` | `bötendönyanıñ` | N | бөтендөнья / adj |
| вазгыятьтә | вазгыять | `wazğıyät` | `wazğıyättä` | N | вазгыять / n |
| вөҗдан | вөҗдансыз | `wöcdansız` | `wöcdan` | N | вөҗдан / n |
| вөҗданнары | вөҗдансыз | `wöcdansız` | `wöcdannarı` | N | вөҗдан / n |
| вөҗданны | вөҗдансыз | `wöcdansız` | `wöcdannı` | N | вөҗдан / n |
| вөҗданның | вөҗдансыз | `wöcdansız` | `wöcdannıñ` | N | вөҗдан / n |
| вөҗданы | вөҗдансыз | `wöcdansız` | `wöcdanı` | N | вөҗдан / n |
| вөҗданым | вөҗдансыз | `wöcdansız` | `wöcdanım` | N | вөҗдан / n |
| вөҗданын | вөҗдансыз | `wöcdansız` | `wöcdanın` | N | вөҗдан / n |
| вөҗданына | вөҗдансыз | `wöcdansız` | `wöcdanına` | N | вөҗдан / n |
| газизовлар | газизов | `Ğazizov` | `Ğazizovlar` | RL | газизов / n |
| газизовны | газизов | `Ğazizov` | `Ğazizovnı` | RL | газизов / n |
| галимовны | галимов | `Ğalimov` | `Ğalimovnı` | RL | галимов / n |
| галимовның | галимов | `Ğalimov` | `Ğalimovnıñ` | RL | галимов / n |
| гами | гам | `ğam` | `ğami` | N | гам / n |
| голәма | голәманы | `ğolämanı` | `ğoläma` | RL | голәма / n |
| голәмалар | голәманы | `ğolämanı` | `ğolämalar` | RL | голәма / n |
| гомумидән | гомуми | `ğomumi` | `ğomumidän` | N | гомуми / adj |
| гореф | горефле | `ğorefle` | `ğoref` | N | гореф / n |
| горефләр | горефле | `ğorefle` | `ğoreflär` | N | гореф / n |
| детальләренә | детальләрен | `detalʼlären` | `detalʼlärenä` | RL | деталь / n |
| диван | диванга | `divanğa` | `divan` | RL | диван / n |
| диваннар | диванга | `divanğa` | `divannar` | RL | диван / n |
| диваны | диванга | `divanğa` | `divanı` | RL | диван / n |
| дәрьялары | дәрья | `däryä` | `däryäları` | N | дәрья / n |
| дәрьяларын | дәрья | `däryä` | `däryäların` | N | дәрья / n |
| дәрьяның | дәрья | `däryä` | `däryänıñ` | N | дәрья / n |
| дәрьясы | дәрья | `däryä` | `däryäsı` | N | дәрья / n |
| дәрьясына | дәрья | `däryä` | `däryäsına` | N | дәрья / n |
| дәрьясында | дәрья | `däryä` | `däryäsında` | N | дәрья / n |
| дөньябыз | дөнья | `dönya` | `dönyabız` | N | дөнья / n |
| дөньябызны | дөнья | `dönya` | `dönyabıznı` | N | дөнья / n |
| дөньяда | дөнья | `dönya` | `dönyada` | N | дөнья / n |
| дөньядан | дөнья | `dönya` | `dönyadan` | N | дөнья / n |
| дөньялар | дөнья | `dönya` | `dönyalar` | N | дөнья / n |
| дөньяларда | дөнья | `dönya` | `dönyalarda` | N | дөнья / n |
| дөньяларны | дөнья | `dönya` | `dönyalarnı` | N | дөнья / n |
| дөньялары | дөнья | `dönya` | `dönyaları` | N | дөнья / n |
| дөньяларын | дөнья | `dönya` | `dönyaların` | N | дөнья / n |
| дөньяны | дөнья | `dönya` | `dönyanı` | N | дөнья / n |
| дөньяның | дөнья | `dönya` | `dönyanıñ` | N | дөнья / n |
| дөньясы | дөнья | `dönya` | `dönyası` | N | дөнья / n |
| дөньясын | дөнья | `dönya` | `dönyasın` | N | дөнья / n |
| дөньясына | дөнья | `dönya` | `dönyasına` | N | дөнья / n |
| дөньясында | дөнья | `dönya` | `dönyasında` | N | дөнья / n |
| дөньясыннан | дөнья | `dönya` | `dönyasınnan` | N | дөнья / n |
| дөньясының | дөнья | `dönya` | `dönyasınıñ` | N | дөнья / n |
| зәкятның | зәкятләребезне | `zäqätlärebezne` | `zäqätnıñ` | N | зәкят / n |
| имлябызның | имля | `imlä` | `imläbıznıñ` | N | имля / n |
| имлясы | имля | `imlä` | `imläsı` | N | имля / n |
| имлясын | имля | `imlä` | `imläsın` | N | имля / n |
| исхаков | исхаковлар | `isxakovlar` | `isxakov` | RL | исхаков / n |
| карусельдә | карусель | `karuselʼ` | `karuselʼdä` | RL | карусель / n |
| кынамыни | кынадыр | `qınadır` | `qınamıni` | N | кына / n |
| көньякның | көньяк | `könyaq` | `könyaqnıñ` | N | көньяк / n |
| көньякта | көньяк | `könyaq` | `könyaqta` | N | көньяк / n |
| көньяктан | көньяк | `könyaq` | `könyaqtan` | N | көньяк / n |
| лирикасының | лирикага | `lirikağa` | `lirikasınıñ` | RL | лирика / n |
| правлениесендә | правлениесенең | `pravleniyeseneñ` | `pravleniyesendä` | RL | правление / n |
| төньякларда | төньяк | `tönyaq` | `tönyaqlarda` | N | төньяк / n |
| төньякта | төньяк | `tönyaq` | `tönyaqta` | N | төньяк / n |
| төньяктан | төньяк | `tönyaq` | `tönyaqtan` | N | төньяк / n |
| факультетларның | факультетындагы | `fakulʼtetındağı` | `fakulʼtetlarnıñ` | RL | факультет / n |
| фатиховның | фатиховны | `fatixovnı` | `fatixovnıñ` | RL | фатихов / n |
| фракциясендә | фракциясенең | `fraktsiyäseneñ` | `fraktsiyäsendä` | RL | фракция / n |
| хакыйкатьләр | хакыйкать | `xaqıyqat` | `xaqıyqatlär` | N | хакыйкать / n |
| хакыйкатьтә | хакыйкать | `xaqıyqat` | `xaqıyqattä` | N | хакыйкать / n |
| хакыйкатьтән | хакыйкать | `xaqıyqat` | `xaqıyqattän` | N | хакыйкать / n |
| цивилизацияләрен | цивилизацияләренең | `tsivilizatsiyäläreneñ` | `tsivilizatsiyälären` | RL | цивилизация / n |
| шагыйрьдә | шагыйрь | `şağir` | `şağirdä` | N | шагыйрь / n |
| шагыйрьләр | шагыйрь | `şağir` | `şağirlär` | N | шагыйрь / n |
| шагыйрьләрдән | шагыйрь | `şağir` | `şağirlärdän` | N | шагыйрь / n |
| шагыйрьләрчә | шагыйрь | `şağir` | `şağirlärçä` | N | шагыйрь / n |
| шәфәкълар | шәфәкъ | `şäfäq` | `şäfäqlar` | N | шәфәкъ / n |
| ядкарьләр | ядкарь | `yädqär` | `yädqärlär` | N | ядкарь / n |
| ядкарьләрдә | ядкарь | `yädqär` | `yädqärlärdä` | N | ядкарь / n |
| җыентыклар | җыентык | `cıyıntıq` | `cıyıntıqlar` | N | җыентык / n |
| җыентыкларда | җыентык | `cıyıntıq` | `cıyıntıqlarda` | N | җыентык / n |
| җыентыкларның | җыентык | `cıyıntıq` | `cıyıntıqlarnıñ` | N | җыентык / n |
| җыентыклары | җыентык | `cıyıntıq` | `cıyıntıqları` | N | җыентык / n |
| җыентыкларын | җыентык | `cıyıntıq` | `cıyıntıqların` | N | җыентык / n |
| җыентыкларына | җыентык | `cıyıntıq` | `cıyıntıqlarına` | N | җыентык / n |
| җыентыкларында | җыентык | `cıyıntıq` | `cıyıntıqlarında` | N | җыентык / n |
| җыентыкны | җыентык | `cıyıntıq` | `cıyıntıqnı` | N | җыентык / n |
| җыентыкның | җыентык | `cıyıntıq` | `cıyıntıqnıñ` | N | җыентык / n |
| җыентыкта | җыентык | `cıyıntıq` | `cıyıntıqta` | N | җыентык / n |
| җәмгыять | җәмгыятькә | `cämğiyätkä` | `cämğiyät` | N | җәмгыять / n |
| җәмгыятьләр | җәмгыятькә | `cämğiyätkä` | `cämğiyätlär` | N | җәмгыять / n |
| җәмгыятьләрдә | җәмгыятькә | `cämğiyätkä` | `cämğiyätlärdä` | N | җәмгыять / n |
| җәмгыятьтә | җәмгыятькә | `cämğiyätkä` | `cämğiyättä` | N | җәмгыять / n |
| җәмгыятьтән | җәмгыятькә | `cämğiyätkä` | `cämğiyättän` | N | җәмгыять / n |
