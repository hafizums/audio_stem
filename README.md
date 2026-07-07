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

### Run tests

```bash
bench --site <your-site> run-tests --app audio_stem
```

#### License

MIT
