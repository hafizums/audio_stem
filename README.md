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

Use a **dedicated test site** when possible. **Do not run tests on `jomveo`** (or any site with manual/production settings and real user jobs) unless you understand that tests temporarily apply defaults during each case — settings are snapshotted and restored, and only tagged test data is cleaned up.

#### Recommended test site setup

```bash
cd /home/hafiz/frappe-bench

bench new-site audio-stem-test.local --admin-password admin
bench --site audio-stem-test.local install-app credit_management
bench --site audio-stem-test.local install-app audio_stem

bench --site audio-stem-test.local run-tests --app audio_stem
```

#### Safe testing guarantees

- Each test **snapshots** `Audio Separation Settings` before making changes and **restores** the exact prior values in `tearDown`.
- Tests create jobs/files tagged with `TEST_AUDIO_STEM` or `test_audio_stem_*` filenames.
- `cleanup_audio_stem_test_data()` runs in `setUp` and `tearDown` and deletes **only** clearly tagged test records.
- Non-test jobs (no marker, non-test filename) are **never** deleted by cleanup.
- Use `temporary_audio_settings(...)` when you need short-lived setting overrides inside a single test block.

#### If you must use an existing site (e.g. `jomveo`)

```bash
bench --site jomveo run-tests --app audio_stem
```

Settings are restored after each test, but prefer a dedicated test site to avoid any risk to manual configuration or real user data.

```bash
bench --site <your-site> run-tests --app audio_stem
```

## Milestone 6

SPA polish, job detail visibility, notifications, onboarding, configuration checklist, and production readiness.

**Current route:** `/audio-vocal-remover` (website SPA). The old Desk route `/app/audio-vocal-remover` is not used.

**Note:** Payment/top-up is intentionally not implemented yet.

### Features

- Job detail panel with previews, downloads, ZIP, retry, timestamps, and cleanup notes
- `get_job_detail` API (owner or System Manager)
- Notification settings: `notify_user_on_completion`, `notify_user_on_failure`
- Safe email + Notification Log on completion/failure
- Onboarding copy, empty states, and progress messaging on the SPA
- Mobile-responsive layout for upload, table, detail panel, and buttons
- System Manager configuration checklist API and SPA admin section
- Production README with setup checklist and troubleshooting

### Notification settings

| Field | Default | Purpose |
| --- | --- | --- |
| `notify_user_on_completion` | 0 | Email + in-app notification when a job completes |
| `notify_user_on_failure` | 0 | Email + in-app notification when a job fails |

Notifications include the job name and a link to `/audio-vocal-remover` only. No API keys or tracebacks are sent.

### Configuration checklist

System Manager API:

```python
frappe.call("audio_stem.api.admin.get_configuration_checklist")
```

Or from bench:

```bash
bench --site <your-site> execute audio_stem.api.admin.get_configuration_checklist
```

The SPA shows this checklist at the bottom of `/audio-vocal-remover` for System Manager users only.

### Setup checklist

1. Install `audio_stem` on the site and run `bench migrate`.
2. Open **Audio Separation Settings** and set `enabled = 1`.
3. Configure the **WaveSpeed API Key** (server-side only).
4. Set `max_file_size_mb` and `max_audio_duration_seconds`.
5. Optionally enable `store_outputs_locally` so provider URLs are copied to private Frappe files.
6. Optionally enable cleanup fields and confirm the daily scheduler is active (`bench doctor` / scheduler enabled).
7. If using credits, install `credit_management`, create a credit type, grant credits manually, then enable `credit_management_enabled`.
8. Build the SPA: `cd apps/audio_stem && yarn build && bench build --app audio_stem`.
9. Run the configuration checklist as System Manager.

### Manual test (Milestone 6)

```bash
cd /path/to/frappe-bench
bench --site <your-site> migrate
cd apps/audio_stem && yarn build
bench build --app audio_stem
bench --site <your-site> clear-cache
bench restart
bench --site <your-site> run-tests --app audio_stem
```

1. Open `/audio-vocal-remover`.
2. Confirm the upload panel shows max file size, max duration, and accepted file types.
3. Upload and complete a short MP3.
4. Open a job from Recent Jobs and confirm the job detail panel shows previews and metadata.
5. Generate ZIP and confirm download works.
6. Enable `notify_user_on_completion`, run another job, and confirm notification/email is sent.
7. Force a failed job with `notify_user_on_failure` enabled and confirm the message is safe.
8. Test mobile width in browser dev tools.
9. Log in as System Manager and confirm the configuration checklist appears with no secrets.
10. Log in as a normal user and confirm the admin checklist is hidden.
11. Run the full test suite.

### Troubleshooting

| Symptom | Likely cause | What to check |
| --- | --- | --- |
| Job stuck in `Queued` | Background worker not running | `bench doctor`, Redis/queue, `bench worker --queue long` |
| Missing worker / no processing | Scheduler or worker down | `bench restart`, confirm long queue worker |
| Insufficient credits | Balance too low | Grant credits in Credit Management; check `credit_status` |
| WaveSpeed API key missing | Settings not configured | Configuration checklist; set key in Audio Separation Settings |
| Duration cannot be detected | Unsupported/corrupt audio | Re-upload MP3/WAV; check file metadata |
| ZIP failed | External URLs expired or files removed | Enable `store_outputs_locally`; retry ZIP on completed job |
| Cleanup did not delete files | Cleanup disabled or retention not reached | `cleanup_enabled`, `retention_days`, job age |
| User did not receive notification | Notifications disabled or email not configured | `notify_user_on_*` flags, site email settings, Error Log |

## Milestone 7

Controlled internal pilot safety: access control, daily limits, cancellation, queue visibility, provider health, audit logs, and abuse protection.

### Pilot mode

| Field | Default | Purpose |
| --- | --- | --- |
| `pilot_mode_enabled` | 0 | When enabled, only allowlisted users/roles can use separation APIs |
| `allowed_users` | — | Newline/comma-separated User emails allowed in pilot |
| `allowed_roles` | — | Newline/comma-separated roles allowed in pilot |
| `blocked_users` | — | Users blocked even if they match an allowed role (System Manager bypasses) |

When pilot mode is off, existing access behavior is unchanged. The SPA at `/audio-vocal-remover` shows a safe blocked message for users who are not allowed.

### Daily usage limits

| Field | Default | Purpose |
| --- | --- | --- |
| `daily_job_limit_per_user` | 0 | Max jobs per user per calendar day (`0` = unlimited) |
| `daily_duration_limit_seconds_per_user` | 0 | Max audio seconds processed per day |
| `daily_cost_limit_usd_per_user` | 0 | Max estimated provider cost per day |

Counts jobs in `Queued`, `Uploading`, `Processing`, and `Completed`. Failed jobs count only when `credit_status = Consumed`. System Manager bypasses limits. Limits are enforced before queueing and shown in the SPA.

### Cancellation

API: `audio_stem.api.separation.cancel_job(job_name)`

- Owner or System Manager only.
- `Draft` / `Queued`: immediate cancel and credit reservation release when credits are enabled.
- `Uploading` / `Processing`: sets `cancellation_requested`; worker stops before provider when possible. If the provider call already finished, the job may still complete safely.
- Job fields: `cancellation_requested`, `cancelled_at`, `cancelled_by`, `cancel_reason`.

### Queue health (System Manager)

API: `audio_stem.api.admin.get_queue_health`

Returns active/queued/uploading/processing counts, oldest active job age, stuck jobs (older than `stuck_job_threshold_minutes`, default 30), recent failures (24h), and worker guidance. The SPA admin section lists stuck jobs without exposing secrets or tracebacks.

### Provider health

Module: `audio_stem.utils.provider_health.get_provider_health_summary`

Uses recent job outcomes only (no paid WaveSpeed health ping). Status: `ok`, `warning`, `error`, or `unknown`.

### Audit log

DocType: **Audio Stem Audit Log** (append-only)

Logs create/start/retry/cancel/complete/fail/ZIP/cleanup/admin views. Does not store API keys, tracebacks, or raw sensitive provider payloads.

### Abuse protection

| Field | Default | Purpose |
| --- | --- | --- |
| `hourly_create_limit_per_user` | 20 | Max job creations per user per hour (`0` = unlimited) |
| `daily_failed_job_limit_per_user` | 10 | Max failed jobs per day before new starts are blocked |

System Manager bypasses abuse limits.

### Manual test (Milestone 7)

```bash
cd /path/to/frappe-bench
bench --site <your-site> migrate
cd apps/audio_stem && yarn build
bench build --app audio_stem
bench --site <your-site> clear-cache
bench restart
bench --site <your-site> run-tests --app audio_stem
```

1. Deploy and migrate.
2. Enable **pilot mode** in Audio Separation Settings.
3. Add one allowed user email.
4. Confirm the allowed user can open `/audio-vocal-remover`.
5. Confirm an unlisted normal user sees the pilot blocked message.
6. Set a low `daily_job_limit_per_user` and confirm the user is blocked after the limit.
7. Start a job and cancel while **Queued**; confirm status `Cancelled` and credits released if enabled.
8. Start another job and request cancel while **Processing**; confirm safe cancellation messaging.
9. Log in as System Manager and review queue health and provider health in the admin section.
10. Confirm **Audio Stem Audit Log** entries are created for key actions.
11. Test mobile layout in browser dev tools.
12. Run the full test suite.

### Troubleshooting (Milestone 7)

| Symptom | Likely cause | What to check |
| --- | --- | --- |
| Pilot user blocked | Not on allowlist or on blocked list | `allowed_users`, `allowed_roles`, `blocked_users` |
| Daily limit reached | Usage counters include queued/completed jobs today | `daily_*` settings; SPA daily usage panel |
| Cancel did not stop processing | Provider call already in flight | `cancellation_requested` on job; safe user message |
| Queue health shows stuck jobs | Worker down or slow provider | `stuck_job_threshold_minutes`; long queue worker |
| Provider health `unknown` | No terminal jobs in last 24h | Recent Completed/Failed jobs |
| Too many creates blocked | Hourly abuse limit | `hourly_create_limit_per_user` |
| Starts blocked after failures | Daily failed-job limit | `daily_failed_job_limit_per_user` |

## Milestone 8

OpenAI Whisper transcription and karaoke subtitle generation (`karaoke_engine` + optional ffmpeg).

### OpenAI Whisper setup

| Field | Default | Purpose |
| --- | --- | --- |
| `openai_enabled` | 0 | Enable backend Whisper transcription |
| `openai_api_key` | — | Password field; never exposed to browser |
| `transcription_model` | `whisper-1` | OpenAI transcription model |
| `transcription_max_file_size_mb` | 25 | Max upload size before ffmpeg compression |
| `transcription_cost_per_minute_usd` | 0 | Estimated cost tracking only unless credits enabled |
| `default_transcription_language` | — | Optional language hint |
| `enable_word_timestamps` | 1 | Request word + segment timestamps |
| `charge_credits_for_transcription` | 0 | Optional credit charge (uses credit client only) |

### Karaoke / karaoke_engine (Milestone 8.1)

| Field | Default | Purpose |
| --- | --- | --- |
| `karaoke_enabled` | 0 | Enable karaoke subtitle pipeline |
| `karaoke_ass_enabled` | 1 | Generate ASS karaoke subtitles |
| `karaoke_video_render_enabled` | 0 | Burn ASS into MP4 with ffmpeg |
| `karaoke_style_preset` | `default_1080p` | `default_1080p`, `default_720p`, `mobile_1080x1920` |
| `karaoke_max_words_per_line` | 5 | Line breaking for ASS |
| `karaoke_video_width` / `karaoke_video_height` | 1080 / 1920 | Background video dimensions |
| `karaoke_background_color` | `#111111` | ffmpeg background color |
| `karaoke_include_instrumental_audio` | 1 | Prefer instrumental audio in karaoke video |
| `karaoke_ffmpeg_preset` | `veryfast` | ffmpeg preset when rendering MP4 |
| `karaoke_ffmpeg_crf` | 18 | ffmpeg CRF when rendering MP4 |
| `charge_credits_for_karaoke` | 0 | Optional credit charge (uses credit client only) |

**Pipelines**

- Transcription: `OpenAI whisper-1 → transcript JSON/SRT/VTT`
- Karaoke subtitles: `transcript JSON → karaoke_engine → ASS`
- Optional video: `ASS + audio/background video → FFmpeg burn-in → MP4`

**System requirements**

- `karaoke_engine` — required for ASS generation (no ffmpeg, PyTorch, CUDA, Playwright, or Chromium)
- `ffmpeg` + `ffprobe` — only required when `karaoke_video_render_enabled` is on

**Install karaoke_engine (bench)**

```bash
cd /path/to/frappe-bench
git clone https://github.com/hafizums/karaoke_engine.git apps/karaoke_engine
./env/bin/pip uninstall -y pycaps pycaps-ai pycaps_ai playwright 2>/dev/null || true
./env/bin/pip install -e apps/karaoke_engine
./env/bin/pip install -e apps/audio_stem
```

`pyproject.toml` references `karaoke_engine @ file:../karaoke_engine` for editable local installs.

### Transcription flow

1. Complete vocal/instrumental separation.
2. In job detail, choose source **Vocal** (default) or **Original**.
3. `start_transcription` queues `process_transcription` on the long worker.
4. Worker calls OpenAI Whisper (`verbose_json` + word timestamps when enabled).
5. Saves transcript text, private JSON/SRT/VTT files on the job.

### Karaoke flow

1. Requires completed transcription with `transcript_json_file` (Whisper verbose JSON).
2. `start_karaoke_render` queues `process_karaoke_render`.
3. Worker builds normalized karaoke word JSON, generates ASS via `karaoke_engine`.
4. If `karaoke_video_render_enabled` is on, creates a background video (when needed) and burns ASS into MP4 with ffmpeg.
5. Attaches private ASS (and optional MP4) to the job. Failed video render keeps the ASS file.

### APIs

- `audio_stem.api.separation.start_transcription(job_name, source="Vocal", language=None)`
- `audio_stem.api.separation.get_transcription_status(job_name)`
- `audio_stem.api.separation.download_transcript_asset(job_name, asset_type)` — `json`, `srt`, `vtt`
- `audio_stem.api.separation.start_karaoke_render(job_name, template=None)` — `template` is deprecated; mapped to `karaoke_style_preset`
- `audio_stem.api.separation.get_karaoke_status(job_name)`

### Manual test (Milestone 8 / 8.1)

```bash
cd /path/to/frappe-bench
bench --site <your-site> migrate
cd apps/audio_stem && yarn build
bench build --app audio_stem
bench --site <your-site> clear-cache
bench restart
bench --site <your-site> run-tests --app audio_stem
```

1. Deploy and migrate.
2. Confirm PyCaps/Playwright/Chromium are **not** in dependency files.
3. Install `karaoke_engine` (see above).
4. Configure OpenAI API key and enable transcription.
5. Complete one audio separation job.
6. Run transcription from **Vocal** source.
7. Generate karaoke ASS subtitle.
8. Download ASS file from job detail.
9. Enable `karaoke_video_render_enabled` and confirm ffmpeg/ffprobe are on PATH.
10. Render karaoke MP4 and confirm preview/download.
11. Disable video render and confirm ASS generation still works without ffmpeg.
12. Run the full test suite.

## Milestone 8.2 — Manual transcript correction

Whisper lyrics timing is often imperfect for music. Milestone 8.2 adds a manual correction workflow **before** ASS/MP4 karaoke rendering. Original Whisper assets are never overwritten.

### Why manual correction exists

- Fix misheard lyrics before karaoke burn-in
- Adjust word/segment timestamps for musical phrasing
- Approve a corrected transcript as the karaoke source

### Original vs manual transcript

| Asset | Field prefix | Mutable |
| --- | --- | --- |
| Whisper (original) | `transcript_*` | No — preserved after transcription |
| Manual corrected | `manual_transcript_*` | Yes — via Transcript Editor |

### Job fields

- `manual_transcript_status`: Not Started / Draft / Saved / Approved
- `manual_transcript_json_file`, `manual_transcript_srt_file`, `manual_transcript_vtt_file`
- `karaoke_use_manual_transcript` (default on)
- `karaoke_source_mode`: Auto / Original Whisper / Manual Corrected

### Settings (subtitle line grouping)

| Field | Default | Purpose |
| --- | --- | --- |
| `subtitle_max_words_per_line` | 5 | Line breaking for manual SRT/VTT and ASS |
| `subtitle_max_line_duration_seconds` | 4.0 | Max line duration guidance |
| `subtitle_min_word_duration_seconds` | 0.08 | Minimum word duration validation |
| `subtitle_snap_overlaps` | 1 | Snap minor word overlaps on save |

### Workflow

```text
Whisper transcript
→ manual correction draft (Transcript Editor)
→ save manual JSON/SRT/VTT
→ approve manual transcript
→ karaoke_engine ASS (source: Auto / Manual / Original)
→ optional FFmpeg MP4
```

### APIs

- `get_transcript_for_edit(job_name)`
- `save_transcript_corrections(job_name, payload)`
- `approve_transcript_corrections(job_name)`
- `reset_manual_transcript(job_name)` — clears manual fields only
- `regenerate_subtitle_assets(job_name, source="manual")` — `manual` or `whisper`
- `download_manual_transcript_asset(job_name, asset_type)` — `json`, `srt`, `vtt`
- `start_karaoke_render(job_name, template=None, karaoke_source_mode=None)`

### Karaoke source modes

- **Auto** — approved manual transcript when `karaoke_use_manual_transcript` is enabled; otherwise Whisper
- **Original Whisper** — always uses `transcript_json_file`
- **Manual Corrected** — requires `manual_transcript_json_file`

### Manual test (Milestone 8.2)

1. Deploy and migrate.
2. Complete separation and Whisper transcription.
3. Open `/audio-vocal-remover` → Job Detail → **Transcript Editor**.
4. Edit a lyric line and adjust a timestamp.
5. **Save Draft** → download manual SRT/VTT.
6. **Approve** manual transcript.
7. Generate karaoke with **Auto** — confirm source shows Manual Corrected.
8. Generate karaoke with **Original Whisper** — confirm Whisper files unchanged.
9. **Reset Manual Edits** — confirm Whisper transcript still exists.
10. Run all tests.

## Milestone 8.3 — Karaoke background video for MP4

Milestone 8.3 lets admins and users (when allowed) choose a **background video** for karaoke MP4 rendering instead of always using a generated solid-color background. ASS subtitle generation remains independent and works without FFmpeg or any background video.

### Background source resolution

When MP4 rendering runs, the server resolves background video in this order:

1. **Job Upload** — `karaoke_background_video_file` on the job
2. **Settings Default** — `default_karaoke_background_video` in Audio Separation Settings
3. **Generated Color** — solid color from `karaoke_background_color` (no video file required)

Clearing the job background makes the pipeline fall back to the settings default, then to generated color.

### Settings fields

| Field | Default | Purpose |
| --- | --- | --- |
| `default_karaoke_background_video` | — | Optional site-wide default background video |
| `allow_user_karaoke_background_upload` | 1 | Allow job owners to upload backgrounds |
| `karaoke_ignore_background_audio` | 1 | Mux instrumental/original audio only |
| `karaoke_loop_background_video` | 1 | Loop short backgrounds to match song length |
| `karaoke_background_blur` | 0 | Mild blur before ASS burn-in |
| `karaoke_background_darken` | 0 | Dark overlay using `karaoke_background_darken_opacity` |
| `karaoke_background_darken_opacity` | 0.25 | Overlay strength when darken is enabled |
| `karaoke_background_fit_mode` | Cover | Cover, Contain, or Stretch |

### FFmpeg / ffprobe

- **ASS only** — no FFmpeg required
- **MP4 with background video** — requires `ffmpeg` and `ffprobe` on PATH when `karaoke_video_render_enabled` is on
- Short backgrounds are **looped** when `karaoke_loop_background_video` is enabled; otherwise output length follows the shorter background (with a safe warning)
- Long backgrounds are **trimmed** to the karaoke audio duration
- Final audio prefers **instrumental** when `karaoke_include_instrumental_audio` is enabled, otherwise **original** audio

### Recommended background video

- **Format:** MP4 (H.264) preferred; MOV, WEBM, and MKV are also accepted
- **Duration:** Match or exceed song length; short clips loop automatically when enabled
- **Resolution:** Any; scaled/cropped to `karaoke_video_width` × `karaoke_video_height` using the configured fit mode

### APIs

- `audio_stem.api.separation.upload_karaoke_background_video(job_name)` — multipart upload
- `audio_stem.api.separation.set_karaoke_background_video(job_name, file_url)` — attach an existing private file
- `audio_stem.api.separation.clear_karaoke_background_video(job_name)` — remove job background
- `audio_stem.api.separation.get_karaoke_status(job_name)` — includes safe background source, filename, note, and upload policy

### Manual test (Milestone 8.3)

```bash
cd /home/hafiz/frappe-bench
bench --site jomveo migrate
cd apps/audio_stem && yarn build
cd /home/hafiz/frappe-bench
bench build --app audio_stem
bench --site jomveo clear-cache
bench restart
bench --site jomveo run-tests --app audio_stem
```

1. Deploy and migrate.
2. Confirm ASS generation still works without a video background.
3. Enable `karaoke_video_render_enabled`.
4. Upload a short MP4 background on a job.
5. Render karaoke MP4 and confirm the background appears behind subtitles.
6. Confirm instrumental audio is used when available.
7. Test a background shorter than the song with looping enabled.
8. Set a default background in settings; clear job background and confirm the default is used.
9. Clear the settings default and confirm generated color fallback.
10. Upload a non-video file and confirm safe rejection.
11. Run all tests.

### Troubleshooting (Milestone 8.3)

| Issue | Cause | Fix |
| --- | --- | --- |
| Unsupported background video | Wrong file type | Use MP4, MOV, WEBM, or MKV |
| ffmpeg missing | Not on PATH | Install ffmpeg; enable video render only when ready |
| ffprobe missing | Not on PATH | Install ffprobe for duration probing |
| Background shorter than song | Loop disabled | Enable `karaoke_loop_background_video` |
| Background audio in output | Ignore flag off | Enable `karaoke_ignore_background_audio` |
| MP4 failed but ASS exists | ffmpeg burn-in error | Download ASS; check `karaoke_error` (no tracebacks exposed) |
| Cannot upload background | Policy or active render | Check `allow_user_karaoke_background_upload`; wait for karaoke to finish |

### Troubleshooting (Milestone 8.2)

| Issue | Cause | Fix |
| --- | --- | --- |
| Transcript Editor disabled | Karaoke render active | Wait for karaoke to finish |
| Approve blocked | Validation errors | Fix timestamps/text in editor |
| Manual Corrected karaoke fails | No manual JSON saved | Save corrections first |
| Auto still uses Whisper | Manual not approved | Approve manual transcript |
| Shift timings rejected | Negative resulting times | Use smaller negative shift |

### Troubleshooting (Milestone 8 / 8.1)

| Symptom | Likely cause | What to check |
| --- | --- | --- |
| Transcription disabled | `openai_enabled` off | Audio Separation Settings |
| Missing API key error | Key not saved | OpenAI API key field; checklist |
| Vocal source blocked | Separation not completed | Job status + vocal output |
| File too large | Over 25 MB default | `transcription_max_file_size_mb`; ffmpeg compression |
| Karaoke disabled | `karaoke_enabled` off | Settings + SPA section |
| ASS disabled | `karaoke_ass_enabled` off | Settings |
| Karaoke needs transcription | Transcription not completed | `transcription_status` + `transcript_json_file` |
| karaoke_engine missing | Package not installed | Admin checklist; `pip install -e apps/karaoke_engine` |
| ffmpeg missing | Not on PATH | Only needed when video render enabled |
| Video render failed, ASS OK | ffmpeg/ffprobe error | `karaoke_error`; Error Log (server-side only) |

### Run tests

See the **Run tests** section near the top of this README for the recommended `audio-stem-test.local` workflow, settings snapshot/restore behavior, and cleanup guarantees.

```bash
bench --site audio-stem-test.local run-tests --app audio_stem
```

#### License

MIT
