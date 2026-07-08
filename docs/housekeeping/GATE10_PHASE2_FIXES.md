# Gate 10 — Phase 2 Fixes

**App:** `audio_stem`  
**Date:** 2026-07-08  
**Scope:** H4, H5, H6, M2 from Gate 9 / Phase 1 follow-up

---

## Fixed items

| ID | Issue | Fix |
|----|-------|-----|
| H4 | Separation retry leaves stale transcription/karaoke | Downstream invalidation on successful separation when prior assets exist |
| H5 | Cleanup gaps | Optional transcript/ZIP/manual transcript deletion + audit log retention |
| H6 | No in-flight transcription/karaoke cancel | Extended `cancel_job` + cooperative worker checkpoints |
| M2 | `credit_error` / reconciliation not visible in SPA | Job payload flags + hero warnings + Admin Credit Reconciliation tab |

---

## Behavior policies

### Downstream stale (H4)

On **successful separation** when the job already had transcription/karaoke/manual transcript assets:

- `transcription_status`, `karaoke_status`, `manual_transcript_status` → `Not Started`
- `downstream_assets_stale = 1` with reason and `downstream_invalidated_at`
- Old file URLs **kept** on the job (audit/legacy)
- API exposes `has_current_*` flags; downloads tab hides stale assets
- `can_zip` false while stale
- Successful **re-transcription** clears stale flag

### Cleanup (H5)

New settings (all default **0** = keep forever):

| Setting | Purpose |
|---------|---------|
| `delete_transcripts_after_retention` | Whisper transcript JSON/SRT/VTT |
| `delete_manual_transcripts_after_retention` | Manual transcript assets |
| `delete_zip_after_retention` | Job ZIP |
| `audit_log_retention_days` | Old `Audio Stem Audit Log` rows |

Rules: terminal jobs only, skip active pipeline jobs, never delete default background video, idempotent `cleanup_notes`.

### Pipeline cancellation (H6)

- `cancel_job` on **Completed** jobs with active transcription/karaoke sets `cancellation_requested`
- Workers check before/after provider steps; no partial new assets committed
- Previous successful assets preserved on cancel
- Status becomes `Cancelled` (not stuck `Processing`/`Rendering`)

### Credit visibility (M2)

- Payload: `credit_error`, `reconciliation_required`
- SPA: warning badge + safe message; System Manager guided to Admin → Credit Reconciliation
- Admin tab lists issues + Retry button (`retry_credit_reconciliation`)

---

## Tests added

**Module:** `audio_stem.tests.test_gate10_phase2`

- Downstream stale on retry success, payload flags, stale cleared after transcription
- Cleanup: transcripts/ZIP/manual/audit on/off, idempotent
- Cancellation: transcription/karaoke checkpoints, API cancel on active transcription
- Credit: reconciliation payload, admin access, retry

---

## Manual test steps

1. Complete separation → transcribe → karaoke on a job.
2. Fail and **retry** separation; confirm stale banner and hidden transcript/karaoke downloads.
3. Re-transcribe; confirm stale banner clears.
4. Start transcription, click **Cancel**; confirm `Cancelled` without new files.
5. Enable cleanup flags on test site; run `bench execute audio_stem.utils.cleanup.cleanup_old_audio_jobs`.
6. Force `Reconciliation Required` credit status; confirm SPA warning and Admin retry.

---

## Remaining risks (Phase 3+)

- `modified`-based retention clock (M9) — not switched to `completed_at`
- ZIP still stem-only scope (M3)
- Preflight before credit reserve (M5)
- Orphan File docs on regeneration (M17)

---

## Recommended Phase 3

1. Retention clock uses completion timestamps  
2. SPA `credit_error` on Recent Jobs list  
3. Transcript/karaoke regen mutex improvements  
4. DB indexes for cleanup/audit queries
