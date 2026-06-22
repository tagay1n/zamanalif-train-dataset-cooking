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

After Gemini pre-annotation, export unique word forms for dictionary-level human
review:

```bash
python -m tatar_preannotator annotation-export \
  --db data/zamanalif.sqlite \
  --output labelstudio_word_review.json \
  --max-items 5000
```

For real annotation batches, enable SQLite tracking so the next export skips
already exported normalized words. Export state is stored in the shared
database by default:

```bash
python -m tatar_preannotator annotation-export \
  --db data/zamanalif.sqlite \
  --output labelstudio_word_review_001.json \
  --max-items 5000 \
  --track-exported
```

Selection rules:

- read annotated Gemini results from `samples` and `preannotation_state` in
  SQLite;
- ignore records with `"tatar": false`;
- export words containing conditional letters `у ү г к в я ю е ц`;
- export `"U"` words even without conditional letters;
- export `"RL"` words only when they contain conditional letters or
  Russian-loan review letters `ё ы ь ъ щ`;
- skip native-looking `"N"` words with mixed front/back vowel harmony;
- defer every normalized word that Gemini marked as a homonym to the later
  sentence-context project;
- deduplicate by lowercase normalized Cyrillic word form.

The output is a Label Studio JSON array:

```json
{
  "data": {
    "id": "word_000001",
    "cyrl_word": "вакытында",
    "auto_zamanalif": "waqıtında",
    "gemini_origin": "N",
    "hints_html": "<ul><li><b>в</b> -> <b>w</b></li><li>Gemini's origin prediction: <b>native</b></li></ul>"
  }
}
```

Accepted convention choices are represented in `auto_zamanalif` with inline
DSL. For example:

```text
orfografi{{IYA|compact=ä|explicit=yä}}
```

`IYA` is the stable rule identifier. `compact` and `explicit` are named
options. The preferred policy currently resolves it to `orfografiyä`; the
compact PDF policy resolves it to `orfografiä`. The DSL marks only the
differing substring.

The command also writes a report JSON. By default it is written as
`<output>.report.json`.

Label Studio layout:

```xml
<View>
  <Header value="Original cyrillic word"/>
  <Text name="cyrl_word" value="$cyrl_word"/>

  <Header value="Hints"/>
  <HyperText name="hints" value="$hints_html"/>

  <Header value="Correct Gemini origin prediction if necessary"/>
  <Choices name="reviewed_origin" toName="cyrl_word" choice="single" required="true">
    <Choice value="N"/>
    <Choice value="RL"/>
    <Choice value="U"/>
  </Choices>

  <Header value="Correct if necessary | ä Ä | ö Ö | ü Ü | ñ Ñ | ı I | ğ Ğ | ş Ş | ç Ç"/>
  <TextArea
    name="corrected_zamanalif"
    toName="cyrl_word"
    rows="1"
    value="$auto_zamanalif"
    placeholder="Edit only if the suggestion is wrong"
    required="true"
  />
</View>
```

The shared SQLite schema includes `reviewed_words` for approved DSL conversion
and reviewed origin. The current increment provides the storage API; importing
completed Label Studio exports into that table is the next annotation-pipeline
step.

## Conversion DSL

The DSL is reserved for competing accepted conventions. It is not used for:

- lexical uncertainty such as deciding `в -> w/v` for a particular word;
- converter uncertainty;
- Russian/Tatar homonyms that require sentence context.

The public DSL helpers are in `tatar_preannotator.conversion`:

- `parse_dsl(value)` validates and parses DSL;
- `resolve_dsl(value, policy)` produces plain Zamanalif;
- `PREFERRED_POLICY` currently selects `IYA=explicit`;
- `PDF_COMPACT_POLICY` selects `IYA=compact`.

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
  --output data/training_compact.jsonl \
  --choice IYA=compact
```

Each output line has only the sentence ID and the text pair:

```json
{"id":"sent_000001","cyrillic":"Орфография.","zamanalif":"Orfografiyä."}
```

The exporter:

- reads `tatar=true` Gemini-annotated sentences from SQLite;
- uses approved `reviewed_words` entries before automatic conversion;
- preserves sentence punctuation, whitespace, and ordinary word casing;
- skips sentences that still contain unreviewed words, mixed-harmony native
  review cases, or contextual homonyms;
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
