## Audio Stem

Frappe app for AI vocal/instrumental separation using WaveSpeed.

## Milestone 1

Internal MVP implemented:

- Audio Separation Settings
- Audio Separation Job
- WaveSpeed backend integration
- Background worker processing
- Start Separation button on the job form
- Vocal and instrumental output URL storage
- Basic tests

## Milestone 2

User-facing vocal remover UI:

- **Dedicated website (Doppio SPA):** `/audio-vocal-remover`

Build the dedicated page:

```bash
cd apps/audio_stem && yarn build
bench build --app audio_stem
bench --site <your-site> clear-cache
```

Dev server: `cd apps/audio_stem && yarn dev` then open `http://<site>:8080/audio-vocal-remover`

- Upload audio and create a job
- Start separation and poll job status
- Preview original, vocal, and instrumental outputs
- Download completed stems
- View recent jobs for the current user

## Milestone 3

Cost-control, validation, retention, and safer processing:

- Enforces upload limits from **Audio Separation Settings**
- Calculates and saves `provider_cost_usd` before queueing
- Limits each user to one active job at a time (System Manager can bypass)
- Idempotent `start_separation` for active/completed jobs
- Safe user-facing `error_message` with full tracebacks in Error Log only
- Optional local output storage via `store_outputs_locally`
- Improved limits and blocked-start messaging on the vocal remover page

### Settings

| Field | Purpose |
| --- | --- |
| `enabled` | Master switch for create/start |
| `wavespeed_api_key` | Server-side WaveSpeed auth only |
| `max_file_size_mb` | Rejects oversized uploads |
| `max_audio_duration_seconds` | Rejects audio longer than the limit |
| `cost_per_second_usd` | Used to estimate and save provider cost |
| `display_currency` | Currency symbol for Desk estimates (default `MYR`) |
| `store_outputs_locally` | Download outputs into private Frappe Files |

### Manual test (Milestone 3)

```bash
cd /path/to/frappe-bench
bench --site <your-site> migrate
bench --site <your-site> clear-cache
cd apps/audio_stem && yarn build
bench build --app audio_stem
bench restart
```

1. Open **Audio Separation Settings** and confirm limits, display currency, and API key.
2. Open `/audio-vocal-remover` and confirm max file size / max duration are shown.
3. Upload a valid audio file and confirm estimated provider cost appears.
4. Start separation and confirm `provider_cost_usd` is saved before the worker runs.
5. Try double-clicking **Start Separation** and confirm only one queue job is created.
6. While a job is active, try starting a second job as the same user and confirm it is blocked.
7. Set `max_audio_duration_seconds` below the uploaded file duration and confirm start is blocked with a clear message.
8. Set `enabled = 0` and confirm create/start are blocked.
9. Optionally enable `store_outputs_locally`, complete a job, and confirm private vocal/instrumental files are attached when download succeeds.

### Known limitation

When `credit_management_enabled` is off, provider cost fields remain informational only.

## Milestone 4

Credit integration with the separate `credit_management` app:

- Optional credit reservation before queueing, consume on success, release on failure
- Integration settings on **Audio Separation Settings**
- Credit fields on **Audio Separation Job**
- Vocal remover page at `/audio-vocal-remover` shows balance and blocks start when credits are insufficient
- All balance changes go through `credit_management.api` only (never mutate ledger rows from `audio_stem`)
- Lazy imports of `credit_management.api` inside integration functions so `audio_stem` still loads when credits are disabled
- Stable idempotency keys: `audio_stem:{job}:reserve|consume|release`

**Warning:** Do not insert or update `Credit Account` or `Credit Ledger Entry` directly from `audio_stem`. Use only:

```python
def some_function():
    import credit_management.api as credit_api
    credit_api.get_balance(...)
    credit_api.reserve_credits(...)
    credit_api.consume_reserved_credits(...)
    credit_api.release_reservation(...)
```

### Credit status flow

| Job stage | Expected `credit_status` |
| --- | --- |
| Draft job created (credits on) | `Pending` |
| Start queued | `Reserved` |
| Worker completed | `Consumed` |
| Worker failed (provider error) | `Released` |
| Worker completed but consume API failed | `Failed` (`credit_error` set, outputs kept) |
| Release API failed after provider failure | `Failed` (`credit_error` set) |
| Credits disabled | `Not Required` |

### Required dependency

Install `credit_management` on the same Frappe site before enabling credit integration.

### Enable credit integration

1. Ensure `credit_management` is installed and has a credit type (preferably `AUDIO_STEM`).
2. Grant credits to users via Credit Management admin tools.
3. Open **Audio Separation Settings** and set:
   - `credit_management_enabled = 1`
   - `credit_type` (default `AUDIO_STEM`)
   - `credit_owner_doctype` (default `User`)

When disabled, internal testing can continue without credits.

### Credit settings fields

| Field | Purpose |
| --- | --- |
| `credit_management_enabled` | Master switch for credit checks and reservations |
| `credit_type` | Credit type code passed to `credit_management.api` |
| `credit_owner_doctype` | Owner DocType for balances (default `User`) |

### Manual test (Milestone 4)

```bash
cd /path/to/frappe-bench
bench --site <your-site> migrate
bench --site <your-site> clear-cache
cd apps/audio_stem && yarn build
bench build --app audio_stem
bench restart
```

1. Install both `audio_stem` and `credit_management` on the site.
2. Ensure `credit_management` has a credit type, preferably `AUDIO_STEM`.
3. Grant credits to a test user using Credit Management admin tools.
4. Enable `credit_management_enabled` in **Audio Separation Settings**.
5. Upload a short MP3 as the test user on `/audio-vocal-remover`.
6. Confirm available balance and estimated job cost are shown.
7. Start separation and confirm credits are reserved (`credit_status = Reserved`).
8. Wait for completion and confirm credits are consumed (`credit_status = Consumed`).
9. Test a failure path (for example invalid API key) and confirm the reservation is released (`credit_status = Released`).

## Milestone 5

Production usability, admin control, retry, ZIP download, cleanup, reporting, and UX polish.

**Note:** Payment/top-up (Stripe, Billplz, subscriptions, public pricing) is intentionally not implemented yet.

### Features

- Enhanced **Recent Jobs** table on `/audio-vocal-remover` with status badges, credit status, costs, output availability, and Open / Retry / ZIP actions
- `retry_failed_job` API for failed jobs only, reusing Milestone 4 credit reservation flow
- `create_job_zip` API for completed jobs (vocal + instrumental, private File)
- Retention settings on **Audio Separation Settings**
- Daily cleanup scheduler (`cleanup_old_audio_jobs`)
- System Manager report: **Audio Stem Usage Summary**
- Improved status messaging and click guards on the vocal remover page

### Retry behavior

- Only `Failed` jobs can be retried (`start_separation` is Draft-only)
- Clears `error_message` and `credit_error` before retry
- Keeps previous output URLs/files until a new run succeeds
- Creates a new credit reservation when prior status was `Released` or `Failed`
- Uses the same reserve → consume/release flow as normal starts

### ZIP download

- Completed jobs only
- Owner or System Manager
- Prefers local `vocal_file` / `instrumental_file`, otherwise downloads external URLs temporarily
- Stores a private ZIP File on the job (`zip_file`)
- Safe user-facing errors only; full tracebacks go to Error Log

### Retention settings

| Field | Default | Purpose |
| --- | --- | --- |
| `cleanup_enabled` | 0 | Master switch for scheduled cleanup |
| `retention_days` | 7 | Age threshold for terminal jobs |
| `delete_original_after_completion` | 0 | Remove original private file for old completed jobs |
| `delete_outputs_after_retention` | 0 | Remove local outputs and clear external URLs after retention |

### Cleanup scheduler

Runs daily via `scheduler_events` when `cleanup_enabled` is on:

- Targets `Completed`, `Failed`, and `Cancelled` jobs older than `retention_days`
- Does not delete job records or credit data
- Idempotent; records notes in `cleanup_notes`

Manual run:

```python
bench --site <your-site> execute audio_stem.utils.cleanup.cleanup_old_audio_jobs
```

### Admin usage report

Open **Audio Stem Usage Summary** as System Manager, or call:

`audio_stem.api.admin.get_audio_stem_usage_summary`

Shows total/completed/failed/active jobs, duration, provider cost, jobs by user, and recent failures.

### Manual test (Milestone 5)

```bash
cd /path/to/frappe-bench
bench --site <your-site> migrate
bench build --app audio_stem
cd apps/audio_stem && yarn build
bench --site <your-site> clear-cache
bench restart
```

1. Open `/audio-vocal-remover`.
2. Upload and complete one short MP3 job.
3. Confirm vocal/instrumental previews work.
4. Create ZIP and confirm both tracks are inside.
5. Force a failed job (for example invalid API key).
6. Confirm **Retry** appears only for the failed job.
7. Retry and confirm it queues safely.
8. If credit integration is enabled, confirm reserve/consume/release still works.
9. Enable cleanup with a short `retention_days`, run cleanup manually, confirm behavior.
10. As System Manager, open **Audio Stem Usage Summary**.
11. As a normal user, confirm global admin metrics are not accessible.

### Run tests

```bash
bench --site <your-site> run-tests --app audio_stem
```

#### License

MIT
