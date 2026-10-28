# Changelog

## [0.1.0] - 2026-10-28

### Added
- `submit(flow, model, [(prompt, item), ...], provider)`: one list of pairs, per-item `prompt_sha` and key, signed manifest with collision-proof name (microseconds + `O_EXCL`); no manifest left behind when the submission fails.
- `collect(path, provider, on_item)`: idempotent and crash-safe (manifest saved atomically after every item); refuses edited manifests, failed jobs and answer counts that do not match; empty answers and per-request errors are errors, not content; matches by echoed key when present, by position otherwise; `on_cost` failures never block writing.
- `collect_pending` with `max_attempts`; `manifest.pending` and `flows` skip unreadable files.
- `GeminiBatch` (inline requests, optional `keys=True` metadata) and `FakeBatch` for tests; `check_config` refuses SDK objects and camelCase keys.
- `parts.text`, `parts.image`, `text_of`, `images_of` for multimodal requests and image answers.
- `Queue`: `batch_queue` table with a partial unique index (one in-flight row per flow and record), `SKIP LOCKED` take, Postgres and SQLite; `submit_queued` and `close_collected`.
- `ledger_hook` to record each answer with llm-cost-kit at the batch price.
- CLI: `status`, `check`, `collect --once/--loop --health`, `ddl`.
