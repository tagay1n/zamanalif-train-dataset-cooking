# Zamanalif Training Dataset Cooking

Build a deterministic N-sentence selection from the Hugging Face
`yasalma/tt-structured-content` dataset, prioritizing Cyrillic letters whose
Zamanalif-2012 conversion depends on context.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Run

```bash
zamanalif-select prepare --output data/candidates.jsonl
zamanalif-select select --candidates data/candidates.jsonl --output data/zamanalif.sqlite
```

`select` writes selected samples into the shared SQLite application database.
The selected sample table is intentionally minimal:

```sql
samples(id, source_id, text)
```

Annotation state is tracked separately in `preannotation_state`. If selected
sample tables already exist, pass `--force` to replace those tables.

The current local application database is `data/zamanalif.sqlite` with 30,000
selected sentences. Inspect the live annotation state with:

```bash
sqlite3 data/zamanalif.sqlite \
  "SELECT COUNT(*) FROM samples; SELECT status, COUNT(*) FROM preannotation_state GROUP BY status;"
```

`prepare` uses bounded windowed harvesting by default. It samples windows from
large documents, prioritizes conditional-letter and mixed-vowel-harmony
sentences, requires at least two Tatar-specific Cyrillic letters (`ә ө ү җ ң һ`)
by default, filters obvious markdown/list/glossary artifacts, and stops at the
configured candidate pool size.

Useful controls:

```bash
zamanalif-select prepare \
  --output data/candidates.jsonl \
  --max-candidates 250000 \
  --max-candidates-per-doc 30 \
  --max-doc-chars 20000 \
  --window-chars 4000 \
  --windows-per-doc 5 \
  --min-tatar-specific-letters 2
```

Use `--exhaustive` only when you intentionally want to scan full document texts.

The selector is deterministic for the same input and `--seed`.

Current annotation inclusion/exclusion decisions are documented in
[docs/annotation_scope.md](docs/annotation_scope.md).

## Gemini Pre-Annotation

Create a private `config.yaml` from `config.example.yaml`. The config must
contain `gemini.model`, `gemini.api_keys`, and all `preannotation` settings.
Missing values fail fast; API keys are not read from environment variables.

Required config shape:

```yaml
gemini:
  model: "gemini-3.5-flash"
  api_keys:
    - "your-key"

preannotation:
  exhausted_keys_path: "data/exhausted_gemini_keys.json"
  requests_per_minute: 5
  graceful_shutdown_timeout_seconds: 300
  initial_batch_size: 30
  request_timeout_seconds: 120
  overload_sleep_seconds: 60
  target_annotated_count: 5000
```

```bash
python -m tatar_preannotator annotate
```

By default, the command reads `config.yaml` and `data/zamanalif.sqlite`.
To override only the configured Gemini model for one run:

```bash
python -m tatar_preannotator annotate --model gemini-2.5-flash
```

To retry every sample previously marked `unprocessable` with another model:

```bash
python -m tatar_preannotator annotate \
  --retry-unprocessable \
  --model <alternative-gemini-model>
```

Retry mode requeues only terminal failures. Existing successful annotations are
not changed. Samples that fail again return to `unprocessable`; successful
retries are saved normally. The database records the successful model in
`preannotation_state.annotated_by_model`. Existing annotations created before
this migration keep a null model value.

The annotator reads pending samples from SQLite, sends adaptive batches to
Gemini, validates the returned JSON schema, and saves valid pre-annotations in
SQLite. Timeouts and invalid JSON shrink the batch size; 503 overload sleeps
and retries; short-window rate-limit errors sleep and retry; quota exhaustion
rotates to the next configured key.
`preannotation.requests_per_minute` controls global request pacing; `5` means
the command waits at least 12 seconds between Gemini request starts.
On first Ctrl+C, the command waits up to
`preannotation.graceful_shutdown_timeout_seconds` for the current Gemini request
to finish, writes normal DB/key state, and exits. Press Ctrl+C again to force
stop immediately.

The command stops when `preannotation.target_annotated_count` is reached, when
there are no pending samples left, or when all configured Gemini keys are
exhausted for the current run.

Quota/rate-limited Gemini keys are written to
`preannotation.exhausted_keys_path` immediately and skipped on the next run.
Remove that JSON file manually when you want to retry those keys.

Successful batch logs print one JSON-like block per sentence. The `tokens`
array stays on one line for readability:

```json
{
  "id": "sent_000001",
  "tatar": true,
  "tokens": [{"text":"Казан","label":"N"},{"text":"проект","label":"RL"}]
}
```

Recommended post-Gemini cleanup order:

1. Run `repair-unprocessable` to locally repair rows that Gemini failed mostly
   because of token alignment around quotes, punctuation, or suffixes.
2. Finish any remaining rows with `manual-preannotate` so every sentence has a
   usable Tatar/non-Tatar and token-origin decision.
3. Run `auto-resolve-conflicts` to save low-risk word conflict decisions.
4. Run `resolve-conflicts` for the remaining meaningful word conflicts.
5. Run `auto-resolve-unknowns` to save low-risk surname/patronymic decisions.
6. Export Label Studio Project 1 word-review tasks with `annotation-export`.

### Local repair of unprocessable rows

Most unprocessable rows are valid Tatar sentences where Gemini returned tokens
that no longer matched the exact original text. Repair them locally before
doing manual sentence work:

```bash
python -m tatar_preannotator repair-unprocessable --dry-run
python -m tatar_preannotator repair-unprocessable
```

The command uses the same tokenizer and label suggestions as
`manual-preannotate`, saves rows as `annotated_by_model = "local-repair"`, and
allows `U` labels because later word-review steps handle uncertain words. It
does not call Gemini and does not touch already successful annotations. Use
`--limit 10` for a small pilot run.

### Manual repair of unprocessable rows

Rows that remain `unprocessable` can be repaired interactively:

```bash
python -m tatar_preannotator manual-preannotate
```

The browser UI starts at `http://127.0.0.1:8765` by default and saves each
accepted row directly into `preannotation_state` with
`annotated_by_model = "manual-web"`. Use `--limit 10` for a short pilot run.
It provides Tatar/non-Tatar controls, per-token `N`/`RL`/`U` radio buttons,
homonym checkboxes, and keyboard shortcuts: arrows move token selection, `1`,
`2`, `3` set labels, Space toggles homonym, and Enter saves.

### Word conflict resolver

Gemini and manual sentence repair can disagree about the same normalized word,
for example a word marked `N` in most sentences but `RL` or `homonym` in a few
noisy rows. Review these conflicts in a separate local browser UI:

```bash
python -m tatar_preannotator auto-resolve-conflicts --dry-run
python -m tatar_preannotator auto-resolve-conflicts
python -m tatar_preannotator resolve-conflicts
python -m tatar_preannotator auto-resolve-unknowns --dry-run
python -m tatar_preannotator auto-resolve-unknowns
```

`auto-resolve-conflicts` writes only conservative decisions to
`word_resolutions`: origin-independent conflicts, tiny `U` noise, and tiny
minority-label noise, plus no-homonym cases where one concrete origin appears
at least 10 times more often than the other. It does not auto-resolve homonym
conflicts and never overwrites existing decisions.

`auto-resolve-unknowns` handles only unresolved `U` words that look like
Russian-style surnames or patronymics, such as `-ов`, `-ев`, `-ова`, `-ева`,
`-ович`, and `-евич` with common Tatar suffixes. It writes them as `RL`, skips
hyphenated compounds and abbreviation/fragments, and never overwrites existing
decisions.

The command starts at `http://127.0.0.1:8766` by default. It shows each
conflicting normalized word, label counts, homonym counts, and example sentence
IDs. Save one word-level decision: `N`, `RL`, `U`, or `contextual_homonym`.
Decisions are stored in `word_resolutions`; original Gemini/manual
`tokens_json` rows are not rewritten.

Resolved `N`/`RL`/`U` decisions are used as cleaned preannotation input for
word-review export. They do not approve final Zamanalif conversion by
themselves; origin-dependent words still need `reviewed_words` approval from
Label Studio Project 1. `contextual_homonym` keeps the word deferred for the
later sentence-context review project.

## Antat Dictionary Reference

Download the Antat English-Tatar dictionary reference into the shared SQLite
database:

```bash
python -m tatar_preannotator download-antat-reference
```

The command downloads source `29` for Cyrillic Tatar meanings and source `30`
for Zamanalif meanings, stores the entry HTML and cleaned text, aligns both
sources by page and position, and always shows a progress bar. By default it
uses `data/zamanalif.sqlite`; pass `--db` only when using another database path.

If Antat reference rows already exist, continue with `--resume` or replace just
the Antat reference tables with `--force`.

## Label Studio Project 1: Word Dictionary Review

After Gemini pre-annotation, export dictionary words and contextual homonym
occurrences into focused Label Studio projects:

```bash
python -m tatar_preannotator annotation-export \
  --db data/zamanalif.sqlite \
  --output-dir labelstudio_projects
```

Exporting does not change annotation state. Re-running the command before
importing completed Label Studio results produces the same eligible tasks.
Only a successful `annotation-import` records completed reviews and excludes
them from later exports. The export writes each project as batches containing
at most 500 tasks by default. Use `--batch-size` to override the file size,
for example `--batch-size 1000` to place up to 1,000 tasks in one JSON file.

Selection rules:

- read annotated Gemini results from `samples` and `preannotation_state` in
  SQLite;
- remove double quotation marks trapped between a word and a recognized Tatar
  suffix (for example, `турында”гы` becomes `турындагы`), preserve
  apostrophes, and quarantine tokens where a quote joins independent words;
- apply saved `word_resolutions` as cleaned preannotation decisions before
  deciding which words need review;
- ignore records with `"tatar": false`;
- compute canonical native (`N`) and loanword (`RL`) DSL for every normalized
  word form;
- export the word only when those branches differ or one branch is unavailable;
- skip words whose conversion is identical under both origins, including `U`
  words, because origin cannot change their target text;
- skip native-looking `"N"` words with mixed front/back vowel harmony, except
  verified lexical hamza families;
- exclude every effective homonym from all dictionary projects, including
  catchall;
- automatically convert homonym occurrences whose native and loanword branches
  are identical;
- export only origin-dependent homonym occurrences to `contextual_homonym` with
  their sentence context;
- collapse repeated unambiguous name initials such as `К.Насыйри` and
  `К. Насыйри` to one contextual task, while keeping references such as
  `К. Ушинский` separate;
- use contextual-only native fallbacks `г → ğ` and `к → q` when an isolated
  homonym has no vowel context; dictionary and catchall conversion stay unchanged;
- encode verified native hamza families with the global `HAMZA` omit/preserve
  policy and route them to `hamza`, never catchall;
- collapse every observed hamza family to one representative task and propagate
  an unchanged policy review to all forms in that exact lexical family;
- always skip forms already approved in `reviewed_words`;
- deduplicate by lowercase normalized Cyrillic word form.

At 20,211 annotated database rows, this decision-based selection produces
45,940 tasks instead of the previous 88,280 letter-based tasks.

Each generated batch is a Label Studio JSON array:

```json
{
  "data": {
    "id": "word_000001",
    "cyrl_word": "вакытында",
    "auto_zamanalif": "waqıtında",
    "gemini_origin": "N",
    "hints_html": "<ul><li>Gemini's origin prediction: <b>native</b></li></ul>"
  }
}
```

Accepted convention choices are preserved internally with inline DSL. For
example:

```text
pro{{E_GLIDE|plain=e|glide=ye}}kt
```

`E_GLIDE` is the stable rule identifier. `plain` and `glide` are named options.
The preferred policy currently resolves it to `proyekt`. Label Studio
annotators see every distinct plain rendering as one editable line in
`zamanalif_variants`, while the DSL and each line's policy mapping are stored
only in task `meta`.

The command writes 500-task batch files such as
`project_e_glide_batch_001_of_003.json`, `project_catchall_batch_001_of_004.json`,
`project_complex_multi_rule_batch_001_of_002.json`, and
`project_hamza_batch_001_of_001.json`, and
`project_contextual_homonym_batch_001_of_027.json`. Import each batch into the
matching Label Studio project. Hamza has routing priority over catchall and
multi-rule projects. Ordinary Russian soft/hard-sign cases (`RUS_SIGN`) now go
to `catchall` with the preferred apostrophe-preserving spelling shown as plain
editable text. The specialized sign-plus-vowel rules `RUS_SIGN_E`,
`RUS_SOFT_SIGN_O`, and `RUS_JOTATION` remain in their focused projects. The
command also writes `project_<key>_instructions.html` for every active
category. Each dictionary word is exported once. If a word has multiple DSL
rules it goes to `complex_multi_rule`; otherwise it goes to the matching
DSL-rule project or to `catchall` (including ordinary `RUS_SIGN` words).
Previously exported focused `rus_sign` and `ts` batches remain importable.
Unresolved `U` words are collected into one focused `unknown_origin` project.
This keeps unknown compounds, abbreviations/fragments, Tatar-specific words,
conditional-letter words, and other unresolved words in one annotation queue.
Words containing `ц` need no special project routing. Cyrillic `ц` now defaults
to `ts` in plain Zamanalif suggestions; `ц` does not control project routing.
Annotators can edit a catch-all suggestion to `s` for a verified lexical
exception.

All unknown-origin tasks receive an editable suggestion from a simple origin
heuristic: a word containing any Tatar-specific Cyrillic letter (`ә`, `ө`, `ү`,
`җ`, `ң`, or `һ`, in either case) uses the native branch; every other word uses
the Russian-loanword branch. The stored Gemini origin remains `U`, and the task
hint identifies the heuristic guess so it is not mistaken for a reviewed
decision. The `unknown_origin` project uses the same single-suggestion labeling
interface as `catchall`; when several convention variants are possible, the
preferred plain rendering is shown for correction. Other focused projects show
all plain Zamanalif variants and keep their DSL policy mapping in task metadata.

The Hints card in `catchall` and `unknown_origin` also shows up to three
distinct source excerpts for the displayed word. Excerpts are selected in
stable corpus order, highlight the occurrence, and include at most twelve
tokens on either side. Ellipses indicate trimmed text. Grouped morphological
tasks use contexts for the displayed representative form only; missing or
unalignable source sentences are skipped.

`--max-items` is applied independently to dictionary words and contextual
occurrences. Contextual tasks are ordered round-robin across homonym words,
with explicit Gemini homonym flags first.

Every export is validated before files are published. Validation checks task
and word uniqueness, project routing, required fields, Zamanalif DSL, and
500-task batch limits, then reads the staged JSON back before publishing it.
When reusing an output directory, the exporter replaces only its managed batch
and instruction files, removes obsolete managed batches, and preserves
unrelated files.

Split tasks keep only labeling-interface values in `data`. Routing metadata is
stored separately so Label Studio does not expose redundant bookkeeping as
task-data columns:

```json
{
  "data": {
    "cyrl_word": "проект",
    "zamanalif_variants": "proyekt\nproekt",
    "gemini_origin": "RL",
    "hints_html": "..."
  },
  "meta": {
    "schema_version": 3,
    "project_key": "e_glide",
    "suggested_zamanalif_dsl": "pro{{E_GLIDE|plain=e|glide=ye}}kt",
    "variant_policies": [
      [{"E_GLIDE": "glide"}],
      [{"E_GLIDE": "plain"}]
    ]
  }
}
```

For focused dictionary projects, each line is editable and must remain in the
same order. An annotator can replace an invalid alternative with `-`; at least
one word must remain, and rejecting alternatives must leave exactly one word.
That surviving spelling becomes an unconditional lexical override, independent
of the active global DSL policy, and propagates to safe family members. Without
`-`, import stores every reviewed line with its hidden policy mapping and
training export selects the corrected line matching the active global policy.
This keeps the annotation interface free of implementation syntax.

Focused dictionary task `data` contains exactly those four fields. Catchall and
`unknown_origin` retain the single `auto_zamanalif` field. Contextual task
`data` additionally contains `sentence`, `context_html`, `native_zamanalif`,
and `loanword_zamanalif`; its `meta` additionally contains `sample_id` and
`token_index`. Project titles, batch fields, DSL rule lists, and custom task
IDs are not repeated in tasks.

Every dictionary project uses the pinned Apertium-tat analyzer to group
unambiguous word forms with the same lemma and part of speech. Predicted origin
and annotation project do not split a family: accepting a representative makes
its known `N` or `RL` origin authoritative for compatible members, including
members predicted `U`. Ordinary propagation requires both surface forms to start with the
full analyzer lemma literally and requires that lemma to contain a
review-sensitive letter. A sibling of any length is covered only when the part
it adds after the forms' shared prefix contains none of the non-deterministic
letters `вгекуцюяүщъьё`. Thus suffix-only ambiguity and surface stem alternation
remain separate tasks. Hamza retains its stricter verified lexical-family
grouping. Candidate DSL policies must be represented by the source review;
otherwise the candidate remains separate. Conflicting direct human origins are
never merged. Previously inherited reviews that fail this rule are exposed
again by export and removed transactionally during the next import.
Install the local toolchain once:

```bash
sudo apt install apertium-all-dev
tools/setup_apertium_tat.sh
```

The compiled language data lives under ignored `.tools/apertium-tat`. Export
and import require it; use `--apertium-tat-dir` only to point at an equivalent
compiled checkout. Ambiguous or unknown analyses stay as single-word tasks.
Family details are not serialized into Label Studio tasks; import reconstructs
them from the displayed representative with the pinned analyzer. Catchall tasks
use `meta.schema_version` `3`.

When a dictionary representative is accepted, import approves covered
same-origin, same-analysis, same-project forms using each form's canonical
conversion. For example, `мәсьәләләрендәге` covers both its prefix chain and
the safe divergent sibling `мәсьәләдә`, but not `мәсьәләгә` because its
divergent suffix contains `г`. Import transfers only edits wholly inside the
canonical Latin prefix shared with a covered member; the member keeps its own
canonical suffix. This applies independently to every focused-project variant.
Thus `казакларының → kazaklarınıñ` can approve `казакларын → kazakların`,
while an edit confined to a longer form's suffix does not propagate. Structured
Suffix-only edits remain representative-only. Historical reviews use the same
safe-family rule. Inherited reviews are recorded in
`reviewed_word_derivations`; exporting itself never writes review state.

Focused-project Label Studio layout (except `unknown_origin`, which uses the
catchall layout with `$auto_zamanalif` and `corrected_zamanalif`):

```xml
<View>
  <Style>
    .box {
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 16px;
      background: #fafafa;
    }

    .box-title,
    .box-title * {
      font-size: 15px !important;
      font-weight: 600 !important;
      color: #555 !important;
      margin-bottom: 8px;
    }

    .big-word,
    .big-word * {
      font-size: 44px !important;
      font-weight: 700 !important;
      font-style: italic !important;
      line-height: 1.3 !important;
    }

    .variant-editor textarea,
    .variant-editor [contenteditable="true"],
    .variant-editor [role="textbox"] {
      font-size: 34px !important;
      font-weight: 700 !important;
      line-height: 1.5 !important;
      min-height: 130px !important;
    }

    .help-text,
    .help-text * {
      color: #666 !important;
      font-size: 14px !important;
    }
  </Style>

  <View className="box">
    <View className="box-title">
      <Text name="original_cyrillic_word_label" value="Original word"/>
    </View>

    <View className="big-word">
      <Text name="cyrl_word" value="$cyrl_word"/>
    </View>
  </View>

  <View className="box"
    visibleWhen="choice-unselected"
    whenTagName="is_homonym"
    whenChoiceValue="Homonym"
  >
    <View className="box-title">
      <Text name="variants_label" value="Check every Zamanalif variant"/>
    </View>

    <View className="help-text">
      <Text
        name="variants_help"
        value="Each line is one variant. Correct it directly, or replace an invalid variant with -. Keep the same line order and at least one word."
      />
    </View>

    <View className="variant-editor">
      <TextArea
        name="reviewed_zamanalif_variants"
        toName="cyrl_word"
        rows="4"
        value="$zamanalif_variants"
        placeholder="One variant per line; use - to reject one"
        editable="true"
        transcription="true"
        maxSubmissions="1"
        required="true"
      />
    </View>

    <Text
      name="fast-copy"
      value="ä Ä | ö Ö | ü Ü | ñ Ñ | ı I | ğ Ğ | ş Ş | ç Ç"
    />
  </View>

  <View className="box">
    <Choices
      name="is_homonym"
      toName="cyrl_word"
      choice="multiple"
      showInline="true"
    >
      <Choice value="Homonym"/>
    </Choices>
  </View>

  <View className="box">
    <Text name="hints_header" value="Hints"/>
    <HyperText name="hints" value="$hints_html"/>
  </View>
</View>
```

The `Homonym` checkbox is unchecked by default. When it remains unchecked,
`reviewed_zamanalif_variants` is required and the exported `gemini_origin` is
retained automatically. When checked, the variant editor is hidden and the
checkbox alone is a complete decision. Dictionary annotations containing a
`reviewed_origin` control are rejected. The textarea keeps one editable
response so reopening an annotation displays all saved variant lines.

The contextual project contains only occurrences whose native and loanword
outputs differ. The annotator selects the meaning in context; the selected
exported variant is accepted automatically unless an optional correction is
entered.

Repeated initials before a capitalized surname are reviewed once per person
reference. Group identity includes the complete initial sequence, the target
initial's position, and the surname; an initial letter alone is never enough.
Whitespace after periods is ignored. Inflected surnames are grouped with an
observed unsuffixed form only for the Tatar genitive, accusative, dative,
locative, and ablative endings. For example, `К. Насыйри`, `К.Насыйриның`, and
`К. Насыйридан` share one decision when the base form occurs in the corpus.
Ambiguous punctuation, reversed name order, lowercase fragments, and unknown
surname stems remain separate tasks.

```xml
<View>
  <Style>
    .box {
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 16px;
      background: #fafafa;
    }

    .box-title,
    .box-title * {
      font-size: 15px !important;
      font-weight: 600 !important;
      color: #555 !important;
      margin-bottom: 8px;
    }

    .context-text,
    .context-text * {
      font-size: 22px !important;
      line-height: 1.6 !important;
    }

    .context-text mark,
    .context-text mark * {
      font-weight: 700 !important;
      font-style: italic !important;
      background: #fff0a6 !important;
      padding: 1px 3px !important;
    }

    .big-word,
    .big-word * {
      font-size: 44px !important;
      font-weight: 700 !important;
      font-style: italic !important;
      line-height: 1.3 !important;
    }

    .variant-word,
    .variant-word * {
      font-size: 32px !important;
      font-weight: 700 !important;
      line-height: 1.35 !important;
    }

    .variant-row {
      margin-bottom: 12px;
    }

    .big-textarea textarea,
    .big-textarea textarea *,
    .big-textarea [contenteditable="true"],
    .big-textarea [role="textbox"] {
      font-size: 44px !important;
      font-weight: 700 !important;
      line-height: 1.3 !important;
      min-height: 64px !important;
    }
  </Style>

  <View className="box">
    <View className="box-title">
      <Text name="sentence_label" value="Sentence"/>
    </View>

    <View className="context-text">
      <HyperText name="context" value="$context_html"/>
    </View>
  </View>

  <View className="box">
    <View className="box-title">
      <Text name="original_cyrillic_word_label" value="Original word"/>
    </View>

    <View className="big-word">
      <Text name="cyrl_word" value="$cyrl_word"/>
    </View>
  </View>

  <View className="box">
    <View className="box-title">
      <Text name="suggested_variants_label" value="Suggested variants"/>
    </View>

    <View className="variant-row">
      <Text name="native_variant_label" value="N - Native"/>
      <View className="variant-word">
        <Text name="native_variant" value="$native_zamanalif"/>
      </View>
    </View>

    <View>
      <Text name="loanword_variant_label" value="RL - Russian / loanword"/>
      <View className="variant-word">
        <Text name="loanword_variant" value="$loanword_zamanalif"/>
      </View>
    </View>
  </View>

  <View className="box">
    <View className="box-title">
      <Text name="origin_choice_label" value="Meaning in this sentence"/>
    </View>

    <Choices
      name="reviewed_origin"
      toName="context"
      choice="single"
      showInline="true"
      required="true"
    >
      <Choice value="N"/>
      <Choice value="RL"/>
    </Choices>
  </View>

  <View className="box">
    <Text
      name="corrected_zamanalif_label"
      value="Correction (only if the selected variant is wrong)"
    />

    <View className="big-textarea">
      <TextArea
        name="corrected_zamanalif"
        toName="context"
        rows="2"
        placeholder="Leave empty to accept the selected variant"
        editable="true"
        maxSubmissions="1"
      />
    </View>

    <Text
      name="fast-copy"
      value="ä Ä | ö Ö | ü Ü | ñ Ñ | ı I | ğ Ğ | ş Ş | ç Ç"
    />
  </View>
</View>
```

`reviewed_origin` is the only required control. When
`corrected_zamanalif` is absent or empty, import stores
`native_zamanalif` for `N` or `loanword_zamanalif` for `RL`. Existing
contextual annotations with a populated correction remain valid.

### Back up annotations from hosted Label Studio

On the hosted Label Studio instance, the UI export can fail when its server-side
`/data/export` directory is unavailable. The task API avoids that directory and
returns task data and annotations directly.

Create a short-lived access token from a Personal Access Token:

```bash
read -rsp "Personal Access Token: " LS_PAT
echo

LS_ACCESS=$(
  printf '{"refresh":"%s"}' "$LS_PAT" |
  curl --silent --show-error --fail \
    -H "Content-Type: application/json" \
    --data-binary @- \
    "https://yasalma-default-annotation.hf.space/api/token/refresh" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access"])'
)
unset LS_PAT
```

Never put the PAT or access token in a file or commit it. List projects first
because Label Studio project IDs can change when a Space or volume is
recreated:

```bash
curl --silent --show-error --fail \
  -H "Authorization: Bearer $LS_ACCESS" \
  "https://yasalma-default-annotation.hf.space/api/projects/?page_size=100" \
  --output data/labelstudio_projects.json

python3 -m json.tool data/labelstudio_projects.json
```

Set the ID shown for the required project and download all task fields. Export
batches in this repository contain at most 500 tasks, so one API page covers
one complete Label Studio project:

```bash
PROJECT_ID=7
OUTPUT="data/labelstudio_project_${PROJECT_ID}_tasks.json"

curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer $LS_ACCESS" \
  "https://yasalma-default-annotation.hf.space/api/tasks/?project=${PROJECT_ID}&fields=all&page_size=1000" \
  --output "${OUTPUT}.part" \
&& mv "${OUTPUT}.part" "$OUTPUT"
```

Keep the `.part` suffix until `curl` succeeds. A failed request therefore
cannot overwrite a previous valid backup. The downloaded JSON is a task API
response with a top-level `tasks` array; both `annotation-audit` and
`annotation-import` accept this shape directly. Replace `PROJECT_ID` for each
Label Studio project.

Validate the backup and inspect actual annotator changes before importing:

```bash
python -m tatar_preannotator annotation-audit \
  --db data/zamanalif.sqlite \
  --input "$OUTPUT"
```

The audit validates completed annotations but does not write to SQLite. It
separates:

- `unchanged`: submitted tasks whose final values equal the exported
  suggestions;
- `changed`: dictionary tasks where the conversion differs, contextual tasks
  where origin or conversion differs, and dictionary homonym decisions;
- `unannotated`: untouched and cancelled tasks.

Only genuine changes are printed after the summary. Label Studio can encode an
edited prefilled TextArea as `["original", "edited"]`; the audit recognizes
this only when the first value exactly matches `data.auto_zamanalif`, then
compares the final value. Merely submitting an unchanged suggestion is not
reported as an edit.

After reviewing every printed change, make a project-specific SQLite backup
and import the same Label Studio backup:

```bash
DB_BACKUP="data/zamanalif.sqlite.before_project${PROJECT_ID}_import"
cp --interactive data/zamanalif.sqlite "$DB_BACKUP"

python -m tatar_preannotator annotation-import \
  --db data/zamanalif.sqlite \
  --input "$OUTPUT"
```

Do not continue if `cp` reports an error. If the backup path already exists,
confirm that replacing it is intentional or choose a new path. The import is
atomic: any validation or family-propagation conflict rolls back the complete
transaction.

The backup must use the task API response schema and contain exactly one
`meta.project_key` with the expected project schema version. Normal dictionary
decisions are written to `reviewed_words`. Dictionary tasks checked as
`Homonym` are instead written to `word_resolutions` as `contextual_homonym`;
their conversion value is ignored. Contextual occurrence decisions are written
to `contextual_reviews` by exact `meta.sample_id` and `meta.token_index`.
For recognized name initials, import reconstructs the group from the database
and writes the same decision to every matching occurrence, including later
batches. Projects exported before initial grouping remain compatible: repeated
tasks are accepted when their decisions agree. Conflicting decisions for one
reference abort the complete import. Consistent historical initial reviews are
also backfilled during contextual import.
Unannotated and cancelled tasks are skipped.
Identical reimports are idempotent; conflicting decisions fail atomically.
After a successful dictionary import, the command prints family-propagation
counts for the current batch and historical backfill, split between literal
subwords and deterministic divergent forms, plus the number of source families.
After contextual audit and import, `initial propagation` reports source groups,
newly propagated occurrences, and historical backfill.

Malformed DSL, missing controls, unexpected dictionary origin controls,
duplicate word tasks, conflicting annotations, or invalid contextual origins
abort the whole import without partial writes. After a successful import,
approved words no longer appear in `annotation-export`.

Remove the access token and temporary shell variables after the import:

```bash
unset LS_ACCESS PROJECT_ID OUTPUT DB_BACKUP
```

## Conversion DSL

The DSL is reserved for competing accepted conventions. It is not used for:

- lexical uncertainty such as deciding `в -> w/v` for a particular word;
- converter uncertainty;
- Russian/Tatar homonyms that require sentence context.

The public DSL helpers are in `tatar_preannotator.conversion`:

- `parse_dsl(value)` validates and parses DSL;
- `resolve_dsl(value, policy)` produces plain Zamanalif;
- `PREFERRED_POLICY` contains the registered default for each remaining rule;
- `PDF_COMPACT_POLICY` contains the selected PDF-oriented alternatives.

Malformed syntax, unknown rules, unknown options, and non-Zamanalif output fail
with an explicit `DslError`.

## Training Dataset Export

DSL is internal annotation data. Final model-training records contain only
ordinary Cyrillic and resolved Zamanalif text.

Export JSONL with the registered default for every DSL rule:

```bash
python -m tatar_preannotator training-export \
  --db data/zamanalif.sqlite \
  --output data/training.jsonl
```

Override a convention with a repeatable `--choice RULE=OPTION` argument:

```bash
python -m tatar_preannotator training-export \
  --db data/zamanalif.sqlite \
  --output data/training_plain_e.jsonl \
  --choice E_GLIDE=plain
```

Each output line has only the sentence ID and the text pair:

```json
{"id":"sent_000001","cyrillic":"Орфография.","zamanalif":"Orfografiyä."}
```

The exporter:

- reads `tatar=true` Gemini-annotated sentences from SQLite;
- uses exact contextual occurrence reviews, then approved `reviewed_words`;
- automatically converts words whose native and loanword branches are
  identical;
- preserves sentence punctuation, whitespace, and ordinary word casing,
  including a closing quote between a converted stem and its Tatar suffix
  (`турында”гы -> turında”ğı`);
- skips sentences that still contain unreviewed origin-dependent words,
  mixed-harmony native review cases, or unresolved contextual occurrences;
- fails without replacing the existing output on malformed DSL, invalid
  policy choices, converter failures, or token alignment errors;
- rejects any final target containing DSL delimiters or Cyrillic letters.

It automatically writes `<output>.manifest.json` with the effective policy,
CLI overrides, exported counts, and not-ready skip counts. Abbreviation-specific
letter-by-letter conversion such as `ЦК -> TsK` is deliberately deferred; this
command currently applies normal word conversion and casing rules.

## Conversion Rules

The conversion and annotator reference lives in
[docs/zamanalif_conversion_rules.md](docs/zamanalif_conversion_rules.md).
It is plain Markdown, so it can also be copied into Label Studio project
instructions.

## Tests

```bash
python3 -m unittest discover -s tests
```
