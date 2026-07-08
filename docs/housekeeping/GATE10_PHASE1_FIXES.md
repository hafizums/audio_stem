# Gate 10 — Phase 1 Fixes

**App:** `audio_stem`  
**Date:** 2026-07-08  
**Scope:** Critical + safest High-priority items from Gate 9 audit

---

## Fixed issues

| Gate 9 ID | Issue | Fix |
|-----------|-------|-----|
| C1 | Credit consume failure leaves reservation stuck | `credit_status` → `Reconciliation Required`; admin helpers + retry API |
| C2 | No ffmpeg subprocess timeout | `karaoke_ffmpeg_timeout_seconds` (default 1800); all `ffmpeg_media` subprocess calls use timeout |
| H1 | Cancel ignored after WaveSpeed returns | Re-check cancellation after provider; cancel + release without attaching outputs |
| H2 | `create_job_from_file` cross-user file gap | `_get_accessible_file_doc` owner/System Manager check |
| H3 | `Cancelled` transcription/karaoke dead end | `Cancelled` added to startable status sets |

---

## Behavior decisions

### Credit reconciliation (C1)

- Separation **outputs are preserved** when consume fails after `Completed`.
- `credit_status` becomes **`Reconciliation Required`** (not generic `Failed`).
- `credit_error` uses `safe_error_message()`; full traceback only in Error Log.
- **No automatic release** after consume failure — the user received separation outputs; admin must **retry consume** via `credit_management_client`.
- Admin APIs (System Manager only):
  - `audio_stem.api.admin.get_credit_reconciliation_issues`
  - `audio_stem.api.admin.retry_credit_reconciliation`
- Retry uses stable idempotency key — safe to call multiple times; no duplicate consume.
- **No direct mutation** of `credit_management` ledger/account tables.

### Cancel after provider (H1)

- If `cancellation_requested` is set when WaveSpeed returns **before** outputs are saved:
  - Job → `Cancelled`
  - Credits **released** (reservation not consumed)
  - Provider URLs are **not** attached to the job
  - Audit log: “Cancellation completed after provider returned.”
- If outputs were already saved before the check (not current code path), they would be preserved — documented for future changes.

### FFmpeg timeout (C2)

- Setting: `karaoke_ffmpeg_timeout_seconds` (Int, default **1800**).
- Applies to: background prep, color video, audio transcode, ffprobe duration probe (capped at 120s).
- `karaoke_engine` MP4 burn-in receives `timeout_seconds` via `RenderOptions`.
- Timeout → safe user message; worker completes with `Failed` or partial `Completed` (ASS preserved).

### File ownership (H2)

- `create_job_from_file` and separation start validation require File owner = session user, job-attached file for same user, or System Manager.

### Cancelled restart (H3)

- `start_transcription` allowed when status ∈ {Not Started, Failed, **Cancelled**}.
- `start_karaoke_render` allowed when status ∈ {Not Started, Failed, **Cancelled**, Completed}.
- Restart clears `transcription_error` / `karaoke_error`; prior assets kept until new generation succeeds.

---

## Tests added

**Module:** `audio_stem.tests.test_gate10_phase1`

| Area | Tests |
|------|-------|
| Credit reconciliation | consume failure preserves outputs + status; admin list; retry consume idempotent; no direct ledger mutation |
| FFmpeg timeout | default setting; subprocess timeout kwarg; safe error; ASS preserved on MP4 timeout |
| Cancel after provider | Cancelled + released; no output URLs; audit log |
| File ownership | owner OK; cross-user blocked; guest blocked; System Manager OK |
| Cancelled restart | transcription/karaoke restart; active duplicate blocked; errors cleared; assets preserved |

**Updated:** `test_milestone4.py` — consume failure expects `Reconciliation Required`.

---

## Remaining risks (Phase 2+)

- H4: Separation retry does not invalidate downstream transcription/karaoke
- H5–H6: Cleanup gaps; in-flight transcription/karaoke cancellation
- H7: Release failure reconciliation (separate from consume)
- H8: Draft jobs with undetected duration
- M2: `credit_error` not shown in SPA

---

## Recommended Phase 2 plan

1. Downstream invalidation on separation retry  
2. Transcript/ZIP cleanup + cooperative cancel in transcription/karaoke workers  
3. SPA surfacing of `credit_error` and reconciliation banner for admins  
4. `redetect_duration` API for H8
