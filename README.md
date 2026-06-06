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
zamanalif-select select --candidates data/candidates.jsonl --output data/selected.sqlite
```

`select` writes a SQLite database instead of Parquet. The selected sample table
is intentionally minimal:

```sql
samples(id, source_id, text)
```

Annotation state is tracked separately in `preannotation_state`. If the output
database already exists, pass `--force` to replace it.

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

```bash
python -m tatar_preannotator annotate
```

The annotator reads pending samples from SQLite, sends adaptive batches to
Gemini, validates the returned JSON schema, and saves valid pre-annotations in
SQLite. Timeouts and invalid JSON shrink the batch size; 503 overload sleeps
and retries; quota/rate-limit errors rotate to the next configured key.

The command stops when `preannotation.target_annotated_count` is reached, when
there are no pending samples left, or when all configured Gemini keys are
exhausted for the current run.

Quota/rate-limited Gemini keys are written to
`preannotation.exhausted_keys_path` immediately and skipped on the next run.
Remove that JSON file manually when you want to retry those keys.

## Tests

```bash
python3 -m unittest discover -s tests
```
