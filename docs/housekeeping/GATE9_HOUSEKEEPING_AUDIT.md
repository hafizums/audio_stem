# Gate 9 — Housekeeping Audit

**App:** `audio_stem`  
**Date:** 2026-07-08  
**Scope:** Milestones 1–8.5 (post per-job karaoke style override)  
**Mode:** Read-only audit first — no feature additions, no payment/top-up, no provider changes

---

## Executive summary

`audio_stem` is a **production-capable MVP** with a coherent architecture: WaveSpeed separation, OpenAI transcription, manual correction, `karaoke_engine` ASS/MP4, credit integration, pilot/limits, cleanup, and a product-style SPA. **847 tests pass** at audit time.

The happy path is well wired and tested (especially separation credits M4, ZIP/retention M5, admin hygiene M8.4, karaoke M8–8.5). However, several **edge paths can leave money, data, or UX in inconsistent states**:

| Area | Verdict |
|------|---------|
| Core upload → separate → transcribe → karaoke → download | Works; gaps on retry/regenerate |
| Credit reserve/consume/release | Mostly correct; **consume failure can strand reservations** |
| Cancellation | Separation pre-provider only; **no mid-provider / mid-transcription / mid-karaoke cancel** |
| Cleanup / retention | Partial; **transcripts, ZIPs, audit logs not fully covered** |
| Security | Strong owner checks; **job create file ownership gap** |
| UX | Good workflow tabs; **missing credit_error, recent-jobs step, duration dead-end** |
| Tests | Broad (847); **worker cancel, stale queue, cross-user file, regen mutex gaps** |
| Dead code | **Unimplemented credit charge flags**; minor duplicates |
| Ops | **No ffmpeg/RQ timeouts**; audit log growth; missing indexes |

### Overall status: **PASS WITH RISKS**

Not a FAIL — the system is shippable for controlled pilot use with System Manager oversight. Address **Critical + High** items before wide production or paid credit rollout.

---

## Critical issues

| # | Issue | Affected flow | Evidence | Suggested fix | Test needed |
|---|-------|---------------|----------|---------------|-------------|
| C1 | **Credit consume failure leaves reservation stuck** while job is `Completed` and `credit_status=Failed` | Credit-enabled separation | `workers/separation_worker.py` `_consume_job_credits_if_needed`; `tests/test_milestone4.py` asserts Failed but not release | On consume failure: retry consume, attempt `release_job_reservation`, admin reconciliation endpoint/alert | Mock consume failure → `reserved_balance` returns to 0 |
| C2 | **No ffmpeg subprocess timeout** — hung transcode blocks `long` queue worker indefinitely | Karaoke MP4, background prep, duration probe | `utils/ffmpeg_media.py` `subprocess.run` without `timeout` | Add configurable timeout; fail job with safe error | Mock hung ffmpeg → worker recovers |

---

## High priority issues

| # | Issue | Severity | Affected flow | Evidence | Suggested fix | Test needed |
|---|-------|----------|---------------|----------|---------------|-------------|
| H1 | **Cancel ignored after WaveSpeed returns** — worker checks cancel before provider call, not after | Separation cancel | `workers/separation_worker.py` L114–116 vs L118–127 | Re-check `should_stop_for_cancellation` after `isolate_vocal_and_instrumental`; finalize cancel + release credits | Cancel during mocked slow provider → `Cancelled`, credits released |
| H2 | **`create_job_from_file` does not verify File owner** — any authenticated user can attach another user's `file_url` | Job create / security | `api/separation.py` `_get_attached_file_doc` (no owner check) vs `_get_accessible_background_file` | Mirror background file access checks for audio uploads | User B cannot create job from User A's private file |
| H3 | **`Cancelled` transcription/karaoke status is a dead end** — not in `TRANSCRIPTION_STARTABLE` / `KARAOKE_STARTABLE` | Transcription / karaoke retry | `utils/transcription_karaoke_controls.py`; workers set `Cancelled` on entry cancel | Include `Cancelled` in startable sets or map to `Failed` | `transcription_status=Cancelled` → `start_transcription` works |
| H4 | **Separation retry does not invalidate downstream** — transcription/karaoke/manual assets can reference old stems | Full pipeline retry | `api/separation.py` `retry_failed_job` → `preserve_outputs=True`; no reset of `transcription_*`, `karaoke_*` | On separation re-queue: reset or mark downstream stale when outputs replaced | Retry after completed transcription → downstream blocked or reset |
| H5 | **Transcript files never deleted on retention** | Cleanup | `utils/cleanup.py` — no `transcript_*`, `manual_transcript_*` fields | Add transcript/manual transcript/ZIP to retention deletion list | Cleanup job removes transcript File docs |
| H6 | **No in-flight transcription/karaoke cancellation API** | Cancel UX | `utils/cancellation.py` `CANCELLABLE_STATUSES` separation-only; workers check cancel once at entry | Extend cancel to transcription/karaoke; cooperative polling in workers | Start transcription → cancel → `Cancelled` |
| H7 | **Credit release failure may leave active reservation** | Cancel / fail | `workers/separation_worker.py` `_release_job_credits_if_needed`; `utils/cancellation.py` | Retry release with backoff; ops alert on `credit_status=Failed` + reservation set | Mock release failure on cancel |
| H8 | **Draft jobs with undetected duration are permanently start-blocked** | Upload → separate | `api/separation.py` swallows duration errors; `utils/limits.py` requires duration | `redetect_duration` API or block create; UI remediation | Upload with bad metadata → recoverable path |
| H9 | **Audit log table grows unbounded** | Operations | `utils/audit_log.py` append-only; `hooks.py` only daily job cleanup for files | Scheduled audit log retention by age | Cron deletes logs > N days |
| H10 | **Missing DB indexes** on hot query columns (`status`, `user`, `modified`, audit `reference_name`) | Operations / scale | DocType JSON — no custom indexes | Add indexes for cleanup, limits, recent jobs, audit queries | Migration + explain on slow queries |

---

## Medium priority issues

| # | Issue | Affected flow | Evidence | Suggested fix |
|---|-------|---------------|----------|---------------|
| M1 | `get_recent_jobs` omits `transcription_status` / `karaoke_status` — Recent Jobs "Step" column wrong | UX | `api/separation.py` `get_recent_jobs` fields; `App.jsx` `RecentJobsTable` | Add fields to API payload |
| M2 | `credit_error` never shown in SPA | Credit UX | Job payload includes field; `JobDetailPanel.jsx` does not render | Show safe message when `credit_status=Failed` |
| M3 | ZIP contains only vocal + instrumental — not ASS/MP4/transcripts | Downloads | `utils/zip_download.py` | Extend ZIP or rename UI to "Stem ZIP" |
| M4 | External WaveSpeed URLs can expire before transcribe/karaoke if `store_outputs_locally=0` | Transcription / karaoke | `workers/separation_worker.py`; `output_storage.py` | Default local storage or re-download on start |
| M5 | Preflight missing at API enqueue (WaveSpeed/OpenAI/ffmpeg/karaoke_engine) — credits may reserve first | Start separation/karaoke | `api/separation.py` `_prepare_and_queue_job` | Preflight before reserve (mirror `config_checklist.py`) |
| M6 | `update_karaoke_style_for_job` has no karaoke-active guard (background upload does) | Style override | `api/separation.py` vs `_assert_karaoke_background_mutable` | Reuse active-karaoke guard |
| M7 | TOCTOU: transcript/background can change after karaoke queued | Karaoke | `workers/karaoke_worker.py` loads inputs at worker start | Snapshot inputs at enqueue |
| M8 | `zip_file` not deleted on retention cleanup | Cleanup | `utils/cleanup.py` | Add `zip_file` to deletion list |
| M9 | Cleanup uses `modified` not `completed_at` — metadata edits postpone retention | Cleanup | `utils/cleanup.py` filters | Use completion timestamps |
| M10 | No re-transcribe after `Completed` — only `Not Started` / `Failed` startable | Transcription | `transcription_karaoke_controls.py` | Add "Re-transcribe" that invalidates prior assets |
| M11 | `Manual Corrected` karaoke does not require manual transcript approval | Karaoke source | `api/separation.py` `start_karaoke_render` | Require `Approved` or document bypass |
| M12 | Pilot bypass on login-only endpoints (`get_karaoke_style_for_job`, etc.) | Pilot access | `api/separation.py` `_require_login` vs `_require_app_access` | Unify access guard |
| M13 | `charge_credits_for_transcription` / `charge_credits_for_karaoke` settings exist but are **unimplemented** | Settings / docs | `audio_separation_settings.json`; README mentions | Implement or mark "future" in README |
| M14 | RQ `long` queue jobs have no `job_timeout` | Operations | `frappe.enqueue` in separation/transcription/karaoke | Configure worker timeout policy |
| M15 | ZIP / HTTP download loads full file into memory | Performance | `zip_download.py`, `transcription_assets.py` | Stream or size limits |
| M16 | Workflow tab does not advance when separation completes (resets only on `job.name`) | UX | `components/WorkflowTabs.jsx` | React to status milestone fields |
| M17 | Re-transcription/karaoke attaches new File rows without deleting superseded docs | Data integrity | `transcription_assets.py`, `karaoke_subtitles.py` `_attach_private_binary_file` | Replace-in-place or orphan cleanup |
| M18 | `regenerate_subtitle_assets` allowed during active karaoke | Transcript regen | `api/separation.py` | Block when `is_karaoke_active` |
| M19 | `needs_regenerate_for_style` not surfaced on job detail payload (only via style API) | UX M8.5 | `karaoke_style_for_job_payload` vs `_transcription_karaoke_payload` | Include flag in job detail for tab switch |

---

## Low priority cleanup

| # | Issue | Evidence | Suggested fix |
|---|-------|----------|---------------|
| L1 | `tempfile.mktemp` used for karaoke intermediates (race-prone) | `karaoke_subtitles.py` | Use `mkstemp` / `NamedTemporaryFile` |
| L2 | `WAVESPEED_API_KEY` written to `os.environ` in client | `integrations/wavespeed_client.py` | Pass key to client only |
| L3 | External audio temp files `delete=False` — cleanup relies on worker `finally` | `karaoke_subtitles.py`, `transcription_assets.py` | Explicit temp tracking |
| L4 | Desk form shows Start for `Failed` jobs but API needs `retry_failed_job` | `audio_separation_job.js` | Fix button to call retry API |
| L5 | Duplicate test module overlap (`test_karaoke_background_video.py` vs `test_milestone8_3.py`) | `tests/` | Consolidate |
| L6 | Maintenance scripts in `tests/` (`delete_all_jobs.py`, `cleanup_legacy_test_data.py`) | `tests/` | Move to `scripts/` or mark non-test |
| L7 | `karaoke_use_manual_transcript` default=1 with no SPA toggle | Job DocType | Expose or remove |
| L8 | `get_context_for_dev()` possibly unused | `www/audio_vocal_remover.py` | Remove or document |
| L9 | README `karaoke_engine` install path may differ from `pyproject.toml` git URL | README vs `pyproject.toml` | Align docs |
| L10 | Stale queue recovery (`*_queue_is_stale`) has no dedicated tests | `transcription_karaoke_controls.py` | Add stale-queue tests |

---

## Functional flow audit (A–E)

### A. No-credit internal flow

| Step | Status | Gaps |
|------|--------|------|
| Upload | OK | Duration undetected → dead-end Draft (H8) |
| Separate | OK | Cancel after provider returns ignored (H1); retry leaves downstream stale (H4) |
| Transcribe | OK | `Cancelled` dead end (H3); no re-transcribe (M10) |
| Edit lyrics | OK | Blocked during active karaoke |
| Generate ASS | OK | Style override + tracking (M8.5) works |
| Render MP4 | OK | ASS-only mode handled; partial MP4 failure preserves prior video |
| Download | OK | ZIP scope limited (M3) |

### B. Credit-enabled flow

| Step | Status | Gaps |
|------|--------|------|
| Cost estimate | OK | Shown in SPA when credit enabled |
| Reserve on queue | OK | Tested M4 |
| Consume on success | OK | **Failure strands reservation (C1)** |
| Release on fail/cancel | Mostly OK | Release failure edge (H7) |
| Retry | OK | Re-reserve path tested M5 |
| `credit_error` in UI | Missing | M2 |

### C. Transcription flow

| Scenario | Status | Gaps |
|----------|--------|------|
| Original vs Vocal source | OK | API allows Original on non-Completed job; UI blocks all until separation done |
| Missing vocal file | OK | Blocked with reason if no URL/file |
| External URL only | OK | Download at worker time; expiry risk (M4) |
| OpenAI disabled | OK | API throws; checklist reports |
| Failed retry | OK | |
| `Cancelled` retry | **Broken** | H3 |
| Manual correction | OK | Approve path tested |

### D. Karaoke flow

| Scenario | Status | Gaps |
|----------|--------|------|
| Original Whisper / Manual / Auto source | OK | Label mismatch when manual saved not approved (M11) |
| ASS only | OK | Completed + informational `karaoke_error` |
| MP4 enabled/disabled | OK | |
| Background upload/default/color | OK | Upload blocked during render |
| Per-job style override | OK | M8.5; API guard missing (M6) |
| Global style fallback | OK | |
| `Cancelled` regenerate | **Broken** | H3 |

### E. Cleanup flow

| Asset | Deleted on retention? | Notes |
|-------|----------------------|-------|
| Original file | Optional (`delete_original_after_completion`) | OK |
| Vocal/instrumental | Yes (`delete_outputs_after_retention`) | OK |
| ASS/MP4/karaoke JSON | Yes | OK |
| Job background video | **Preserved** | By design; note in cleanup |
| Transcripts | **No** | H5 |
| Manual transcripts | **No** | H5 |
| ZIP | **No** | M8 |
| Audit logs | **No** | H9 |
| Job records | Never deleted | By design |

---

## Data integrity findings

| Issue | Severity | Notes |
|-------|----------|-------|
| Orphan File docs on re-transcription/karaoke | Medium | New File per render without deleting old |
| Stale outputs after failed separation retry | Medium | `preserve_outputs=True` |
| `karaoke_effective_style_json` vs live override drift | Low | UI warns via `needs_regenerate_for_style` |
| `modified`-based retention clock | Medium | Edits postpone cleanup |
| Unused `charge_credits_for_*` settings | Medium | Misleading configuration |
| PyCaps-era fields removed | OK | No stale PyCaps fields found in active code |

---

## Security and permission audit

| Check | Status | Notes |
|-------|--------|-------|
| Owner-only job access | **Pass** | `_get_job_for_user`; cross-user tests exist |
| System Manager bypass | **Pass** | Tested for transcription/karaoke |
| Guest blocked | **Pass** | Whitelist + `_require_login` / `_require_app_access` |
| Server paths in API payloads | **Pass** | M8.3/M8.4 tests |
| API keys in payloads | **Pass** | Static + payload tests |
| Raw tracebacks to client | **Pass** | `safe_error_message` pattern |
| Cross-user file on job create | **Fail** | H2 |
| Cross-user background video | **Pass** | `_get_accessible_background_file` |
| Cross-user transcript download | **Pass** | Owner check on job |
| Direct credit table mutation | **Pass** | `credit_management_client` only |
| Frontend OpenAI/WaveSpeed calls | **Pass** | SPA uses Frappe APIs only |
| Audit log secrets | **Pass** | Sanitization in `audit_log.py` |
| Pilot on all endpoints | **Partial** | M12 |

---

## UX audit recommendations

| State | Current | Recommendation |
|-------|---------|----------------|
| Empty state | OK | `EmptyJobState` |
| Upload loading | OK | |
| Separation active | OK | Polling + status pill |
| Transcription active | OK | |
| Karaoke active | OK | |
| Cancellation requested | Partial | No cancel for transcription/karaoke |
| Failed job | OK | Retry button |
| Insufficient credits | OK | Blocked reason in utils |
| Pilot blocked | Partial | After job exists, drafts linger |
| Daily limit exceeded | OK | |
| ffmpeg unavailable | Partial | Fails at worker; no upfront SPA message |
| OpenAI unavailable | Partial | Checklist/admin; limited user message |
| karaoke_engine unavailable | Partial | Fail at karaoke start |
| No background selected | OK | Falls back to color |
| Style changed not regenerated | OK | M8.5 warning in `KaraokeStyleCard` |
| Transcript changed not regenerated | Partial | Karaoke tab shows label mismatch warning |
| Mobile layout | OK | Responsive grid; not deeply tested |
| Recent jobs step column | **Wrong** | M1 |
| `credit_error` display | **Missing** | M2 |
| Duration undetected warning | Partial | Amber text; no action | H8 |

---

## Test coverage audit

**Current:** ~847 tests across milestones M3–M8.5, base helpers, separation, credits, cleanup, karaoke, UI hygiene.

### Recommended additional tests (by module)

#### Credits (M4 extension)
- Consume failure triggers release or reconciliation
- Release failure alert path
- Enqueue failure on retry releases reservation

#### Cancellation (M7 extension)
- Cancel after mocked WaveSpeed return
- Transcription worker mid-flight cancel
- Karaoke worker mid-flight cancel
- `Cancelled` → restart transcription/karaoke

#### Security
- Cross-user `create_job_from_file`
- Pilot blocked on `update_karaoke_style_for_job`
- Parallel `start_separation` same job (stress)

#### Transcription / karaoke (M8 extension)
- Stale `Queued` recovery (`transcription_queue_is_stale`, `karaoke_queue_is_stale`)
- Re-transcription orphan File cleanup
- `regenerate_subtitle_assets` during active karaoke blocked
- Expired external vocal URL

#### Karaoke / style (M8.5 extension)
- Style update blocked during `Rendering`
- Background change after MP4 → needs regen flag
- Full regen cycle: override → save → render → `karaoke_style_source=Job Override`

#### Cleanup (M5 extension)
- Transcript + manual transcript + ZIP deletion
- Cleanup does not touch non-terminal active jobs (explicit)

#### Operations
- ffmpeg timeout behavior
- Audit log retention job

#### Notifications (M6 extension)
- Notification failure does not fail job (if applicable)

---

## Dead code and stale dependency audit

| Finding | Type | Path | Action |
|---------|------|------|--------|
| PyCaps / Playwright / Chromium | Absent | — | OK; guarded by tests |
| `charge_credits_for_transcription` | Unimplemented setting | `audio_separation_settings.json` | Document or implement |
| `charge_credits_for_karaoke` | Unimplemented setting | Same | Document or implement |
| `test_karaoke_background_video.py` | Duplicate coverage | `tests/` | Consolidate |
| `resolve_karaoke_background_video` vs `resolve_karaoke_background_video_path` | Overlap | `karaoke_backgrounds.py`, `karaoke_subtitles.py` | Optional merge |
| `/app/audio-vocal-remover` Desk page | Removed | `patches/v1_0/remove_desk_audio_vocal_remover_page.py` | OK |
| SPA route `/audio-vocal-remover` | Active | `www/audio-vocal-remover.html` | OK |
| All React components | Used | `audio-vocal-remover/src/` | No dead components |
| `delete_all_jobs.py` in tests | Maintenance script | `tests/` | Relocate |
| `cleanup_legacy_test_data.py` | Maintenance script | `tests/` | Relocate |

---

## Performance and operations audit

| Risk | Severity | Detail |
|------|----------|--------|
| ffmpeg no timeout | Critical | C2 |
| RQ no job timeout | High | M14 |
| ZIP full-memory buffering | High | M15 |
| Concurrent karaoke dedupe by `job_id` only | Medium | Per-job OK; no global cap |
| Cleanup batch `limit=500` | Medium | Large backlogs need multiple days |
| Error Log per failure | Medium | ~13 `frappe.log_error` sites |
| Audit log unbounded | High | H9 |
| Missing DB indexes | High | H10 |
| Temp file patterns | Low | L1–L3 |

---

## Recommended next gate plan (Gate 10)

### Phase 1 — Production safety (1–2 weeks)
1. **C1** Credit consume failure reconciliation
2. **H1** Post-provider cancellation check
3. **H2** File ownership on job create
4. **H3** `Cancelled` status restart policy
5. **C2** ffmpeg timeout

### Phase 2 — Data hygiene (1 week)
6. **H4** Separation retry invalidates downstream
7. **H5/M8** Cleanup transcripts + ZIP
8. **H6** Transcription/karaoke cancel API
9. **M6** Style update mutex

### Phase 3 — UX polish (1 week)
10. **M1/M2/M16** Recent jobs step, credit_error, workflow tab
11. **H8** Duration recovery
12. **M3** ZIP scope clarity

### Phase 4 — Operations (ongoing)
13. **H9/H10** Audit retention + indexes
14. **M13** Credit charge flags — implement or document
15. Test gap closure per list above

---

## Audit methodology

- Read-only code review of API (`separation.py`), workers, utils, DocTypes, SPA (`App.jsx`, `JobDetailPanel.jsx`, `KaraokeStyleCard.jsx`)
- Cross-check against existing test modules M3–M8.5
- Grep for forbidden dependencies (PyCaps, Playwright, Chromium) — **none in runtime**
- No code changes made during audit except this report
- **847 tests pass** at audit time (`bench --site jomveo run-tests --app audio_stem`)

---

## Acceptance checklist

| Criterion | Met? |
|-----------|------|
| Audit report exists | Yes — this file |
| No new feature added | Yes |
| No payment/top-up added | Yes |
| No provider architecture changed | Yes |
| Existing tests still pass | Yes (847) |
| Report clearly states what to fix next | Yes — Gate 10 plan |

---

*End of Gate 9 Housekeeping Audit*
