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

User-facing Desk page at `/app/audio-vocal-remover`:

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
- Improved limits and blocked-start messaging on the Desk page

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
bench build --app audio_stem
bench restart
```

1. Open **Audio Separation Settings** and confirm limits, display currency, and API key.
2. Open `/app/audio-vocal-remover` and confirm max file size / max duration are shown.
3. Upload a valid audio file and confirm estimated provider cost appears.
4. Start separation and confirm `provider_cost_usd` is saved before the worker runs.
5. Try double-clicking **Start Separation** and confirm only one queue job is created.
6. While a job is active, try starting a second job as the same user and confirm it is blocked.
7. Set `max_audio_duration_seconds` below the uploaded file duration and confirm start is blocked with a clear message.
8. Set `enabled = 0` and confirm create/start are blocked.
9. Optionally enable `store_outputs_locally`, complete a job, and confirm private vocal/instrumental files are attached when download succeeds.

### Known limitation

There is still **no payment or credit system**. Provider cost fields are informational controls only.

### Run tests

```bash
bench --site <your-site> run-tests --app audio_stem
```

#### License

MIT
