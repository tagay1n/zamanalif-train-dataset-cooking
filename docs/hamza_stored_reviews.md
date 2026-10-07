# Stored reviews containing the retired HAMZA rule

Snapshot date: 2026-10-07. Source: `data/zamanalif.sqlite`, table `reviewed_words`.

**Preferred policy: preserve hamza wherever possible**, using modifier letter apostrophe `ʼ` (U+02BC). For these 114 entries, the old review explicitly identifies the hamza position, so the proposed spelling selects `preserve=ʼ` at that position and keeps all other reviewed text unchanged.

Migration completed on 2026-10-07 at `10:21:32.966948 UTC`: all 114 database reviews now use the preserved spellings below. Only `zamanalif_dsl` and `updated_at` were changed; origins and all other reviewed text were retained. All 47,148 reviewed words and 478 reviewed variants pass the training export dictionary validator.

The converter was subsequently updated on 2026-10-07 to preserve hamza deterministically in all nine verified lexical spellings, including suffix forms and individual hyphenated components in both origin branches. Existing approved reviews remain authoritative and were not rewritten by this converter change.

One separately approved review outside the 114 migrated rows still omits hamza: `иэтиляф` is stored as `itiläf`, while the updated native converter produces `iʼtiläf`. Training export continues to use that existing review.

The stored DSL column retains the original values from before migration. A restorable backup of the original rows, including their timestamps, is in `data/hamza_reviews_before_preserve_20261007T102132966948Z.json` (local, ignored by Git).

All 114 original records had `updated_at = 2026-08-12T12:12:10.941782+00:00`; their migration timestamp is `2026-10-07T10:21:32.966948+00:00`. Origins: 112 `N`, 2 `RL`.

`HAMZA` was retired in commit `b1b87af` on 2026-09-19. The current validator rejects each original stored value with `unknown rule id: HAMZA`.

## Family counts

| Family | Entries |
| --- | ---: |
| коръән | 23 |
| мәсьәлә | 35 |
| мөэмин | 16 |
| таэмин | 1 |
| тәэмин | 14 |
| тәэсир | 24 |
| җөрьәт | 1 |
| **Total** | **114** |

## All 114 entries

| No. | Cyrillic word form | Origin | Original stored DSL | Migrated spelling with hamza preserved |
| ---: | --- | --- | --- | --- |
| 1 | коръән | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}än` | `qorʼän` |
| 2 | коръән-кәримнең | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}än-kärimneñ` | `qorʼän-kärimneñ` |
| 3 | коръән-хафиз | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}än-xafiz` | `qorʼän-xafiz` |
| 4 | коръән-хафизлар | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}än-xafizlar` | `qorʼän-xafizlar` |
| 5 | коръән-хафизларны | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}än-xafizlarnı` | `qorʼän-xafizlarnı` |
| 6 | коръәнгә | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}ängä` | `qorʼängä` |
| 7 | коръәндә | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}ändä` | `qorʼändä` |
| 8 | коръәндәге | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}ändäge` | `qorʼändäge` |
| 9 | коръәне | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}äne` | `qorʼäne` |
| 10 | коръәненә | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}änenä` | `qorʼänenä` |
| 11 | коръәни | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}äni` | `qorʼäni` |
| 12 | коръәни-кәрим | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}äni-kärim` | `qorʼäni-kärim` |
| 13 | коръәни-кәримдә | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}äni-kärimdä` | `qorʼäni-kärimdä` |
| 14 | коръәни-кәримнең | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}äni-kärimneñ` | `qorʼäni-kärimneñ` |
| 15 | коръәни-кәримнән | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}äni-kärimnän` | `qorʼäni-kärimnän` |
| 16 | коръәнийә | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}äniyä` | `qorʼäniyä` |
| 17 | коръәниядән | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}äniädän` | `qorʼäniädän` |
| 18 | коръәнияи | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}äniäi` | `qorʼäniäi` |
| 19 | коръәнне | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}änne` | `qorʼänne` |
| 20 | коръәннең | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}änneñ` | `qorʼänneñ` |
| 21 | коръәннән | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}ännän` | `qorʼännän` |
| 22 | коръәннәрдәге | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}ännärdäge` | `qorʼännärdäge` |
| 23 | коръәнхафизлар | N | `qor{{HAMZA\|omit=\|preserve=ʼ}}änxafizlar` | `qorʼänxafizlar` |
| 24 | мәсьәлә | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älä` | `mäsʼälä` |
| 25 | мәсьәлә-ләрдә | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älä-lärdä` | `mäsʼälä-lärdä` |
| 26 | мәсьәлә-мисалларны | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älä-misallarnı` | `mäsʼälä-misallarnı` |
| 27 | мәсьәләгә | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älägä` | `mäsʼälägä` |
| 28 | мәсьәләдер | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläder` | `mäsʼäläder` |
| 29 | мәсьәләдә | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älädä` | `mäsʼälädä` |
| 30 | мәсьәләдәге | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älädäge` | `mäsʼälädäge` |
| 31 | мәсьәләләр | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älälär` | `mäsʼälälär` |
| 32 | мәсьәләләргә | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älälärgä` | `mäsʼälälärgä` |
| 33 | мәсьәләләрдә | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älälärdä` | `mäsʼälälärdä` |
| 34 | мәсьәләләрдән | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älälärdän` | `mäsʼälälärdän` |
| 35 | мәсьәләләре | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläläre` | `mäsʼäläläre` |
| 36 | мәсьәләләремезгә | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläläremezgä` | `mäsʼäläläremezgä` |
| 37 | мәсьәләләрен | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älälären` | `mäsʼälälären` |
| 38 | мәсьәләләрендә | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älälärendä` | `mäsʼälälärendä` |
| 39 | мәсьәләләрендәге | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älälärendäge` | `mäsʼälälärendäge` |
| 40 | мәсьәләләренең | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläläreneñ` | `mäsʼäläläreneñ` |
| 41 | мәсьәләләреннән | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älälärennän` | `mäsʼälälärennän` |
| 42 | мәсьәләләренә | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älälärenä` | `mäsʼälälärenä` |
| 43 | мәсьәләләрне | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älälärne` | `mäsʼälälärne` |
| 44 | мәсьәләләрнен | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älälärnen` | `mäsʼälälärnen` |
| 45 | мәсьәләләрнең | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älälärneñ` | `mäsʼälälärneñ` |
| 46 | мәсьәләне | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläne` | `mäsʼäläne` |
| 47 | мәсьәләнең | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläneñ` | `mäsʼäläneñ` |
| 48 | мәсьәләр | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älär` | `mäsʼälär` |
| 49 | мәсьәләре | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläre` | `mäsʼäläre` |
| 50 | мәсьәләрне | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}älärne` | `mäsʼälärne` |
| 51 | мәсьәләсе | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläse` | `mäsʼäläse` |
| 52 | мәсьәләсен | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläsen` | `mäsʼäläsen` |
| 53 | мәсьәләсендә | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläsendä` | `mäsʼäläsendä` |
| 54 | мәсьәләсендәге | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläsendäge` | `mäsʼäläsendäge` |
| 55 | мәсьәләсене | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläsene` | `mäsʼäläsene` |
| 56 | мәсьәләсенең | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläseneñ` | `mäsʼäläseneñ` |
| 57 | мәсьәләсенә | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläsenä` | `mäsʼäläsenä` |
| 58 | мәсьәләңне | N | `mäs{{HAMZA\|omit=\|preserve=ʼ}}äläñne` | `mäsʼäläñne` |
| 59 | мөэмин | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}min` | `möʼmin` |
| 60 | мөэмин-каратай | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}min-qaratay` | `möʼmin-qaratay` |
| 61 | мөэмин-мөселман | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}min-möselman` | `möʼmin-möselman` |
| 62 | мөэмин-мөселманга | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}min-möselmanğa` | `möʼmin-möselmanğa` |
| 63 | мөэмин-мөселманнарга | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}min-möselmannarğa` | `möʼmin-möselmannarğa` |
| 64 | мөэмин-мөселманның | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}min-möselmannıñ` | `möʼmin-möselmannıñ` |
| 65 | мөэмин-мөэминә | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}min-möeminä` | `möʼmin-möeminä` |
| 66 | мөэминин | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}minin` | `möʼminin` |
| 67 | мөэминнәр | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}minnär` | `möʼminnär` |
| 68 | мөэминнәргәдер | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}minnärgäder` | `möʼminnärgäder` |
| 69 | мөэминнәреннән | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}minnärennän` | `möʼminnärennän` |
| 70 | мөэминнәрне | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}minnärne` | `möʼminnärne` |
| 71 | мөэминнәрнең | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}minnärneñ` | `möʼminnärneñ` |
| 72 | мөэминова | RL | `mö{{HAMZA\|omit=\|preserve=ʼ}}minova` | `möʼminova` |
| 73 | мөэминованың | RL | `mö{{HAMZA\|omit=\|preserve=ʼ}}minovanıñ` | `möʼminovanıñ` |
| 74 | мөэминә | N | `mö{{HAMZA\|omit=\|preserve=ʼ}}minä` | `möʼminä` |
| 75 | таэмин | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}min` | `täʼmin` |
| 76 | тәэмин | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}min` | `täʼmin` |
| 77 | тәэминат | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}minat` | `täʼminat` |
| 78 | тәэминатка | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}minatqa` | `täʼminatqa` |
| 79 | тәэминатның | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}minatnıñ` | `täʼminatnıñ` |
| 80 | тәэминатчы | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}minatçı` | `täʼminatçı` |
| 81 | тәэминатчылар | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}minatçılar` | `täʼminatçılar` |
| 82 | тәэминатчыларны | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}minatçılarnı` | `täʼminatçılarnı` |
| 83 | тәэминатчыларның | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}minatçılarnıñ` | `täʼminatçılarnıñ` |
| 84 | тәэминатчыны | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}minatçını` | `täʼminatçını` |
| 85 | тәэминаты | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}minatı` | `täʼminatı` |
| 86 | тәэминатын | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}minatın` | `täʼminatın` |
| 87 | тәэминатына | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}minatına` | `täʼminatına` |
| 88 | тәэминатында | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}minatında` | `täʼminatında` |
| 89 | тәэминатыннан | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}minatınnan` | `täʼminatınnan` |
| 90 | тәэсир | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sir` | `täʼsir` |
| 91 | тәэсир­ләнеп | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirlänep` | `täʼsirlänep` |
| 92 | тәэсире | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sire` | `täʼsire` |
| 93 | тәэсирен | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}siren` | `täʼsiren` |
| 94 | тәэсирендә | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirendä` | `täʼsirendä` |
| 95 | тәэсирендәге | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirendäge` | `täʼsirendäge` |
| 96 | тәэсиреннән | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirennän` | `täʼsirennän` |
| 97 | тәэсиренә | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirenä` | `täʼsirenä` |
| 98 | тәэсиренәме | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirenäme` | `täʼsirenäme` |
| 99 | тәэсирле | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirle` | `täʼsirle` |
| 100 | тәэсирлелек | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirlelek` | `täʼsirlelek` |
| 101 | тәэсирлерәк | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirleräk` | `täʼsirleräk` |
| 102 | тәэсирләнгәнлегемне | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirlängänlegemne` | `täʼsirlängänlegemne` |
| 103 | тәэсирләндергән | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirländergän` | `täʼsirländergän` |
| 104 | тәэсирләндерде | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirländerde` | `täʼsirländerde` |
| 105 | тәэсирләнүеннән | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirlänüennän` | `täʼsirlänüennän` |
| 106 | тәэсирләнүчән | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirlänüçän` | `täʼsirlänüçän` |
| 107 | тәэсирләр | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirlär` | `täʼsirlär` |
| 108 | тәэсирләре | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirläre` | `täʼsirläre` |
| 109 | тәэсирләрем | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirlärem` | `täʼsirlärem` |
| 110 | тәэсирләрен | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirlären` | `täʼsirlären` |
| 111 | тәэсирләшүе | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirläşüe` | `täʼsirläşüe` |
| 112 | тәэсирне | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirne` | `täʼsirne` |
| 113 | тәэсирнең | N | `tä{{HAMZA\|omit=\|preserve=ʼ}}sirneñ` | `täʼsirneñ` |
| 114 | җөрьәт | N | `cör{{HAMZA\|omit=\|preserve=ʼ}}ät` | `cörʼät` |

## Other spelling differences to retain for separate review

At migration time, four saved spellings differed from the converter in other letters: `коръәниядән` and `коръәнияи` lacked a `y` that the converter added, and the `RL` forms `мөэминова` and `мөэминованың` had a saved `mömin…` stem rather than `möemin…`. The later converter change now agrees with the preserved surname spellings. The two `y` differences remain; the converter also now preserves both hamza positions in `мөэмин-мөэминә`, whereas the historical review marked only the first. The migrated values above retain the saved text and insert only the apostrophe explicitly identified by the original DSL. These other differences were not overwritten during cleanup.
