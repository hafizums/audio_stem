# Gate 10.1 — Transcription Quality Hardening

**Status:** Implemented  
**Date:** 2026-07-08  
**Scope:** Improve Whisper transcription quality and reliability for song/karaoke use without replacing manual correction.

---

## Summary

Gate 10.1 adds configurable preprocessing, prompts, optional chunking, quality diagnostics, and Transcribe tab UX improvements. Whisper remains a **draft** lyric source; the Edit Lyrics workflow is unchanged.

---

## What changed

### Settings (`Audio Separation Settings`)

| Field | Default | Purpose |
| --- | --- | --- |
| `transcription_audio_preprocess_enabled` | 1 | Mono compressed audio + loudness normalize before Whisper |
| `transcription_preprocess_sample_rate` | 16000 | Preprocess sample rate |
| `transcription_preprocess_channels` | 1 | Preprocess channels |
| `transcription_preprocess_bitrate` | 64k | Preprocess bitrate |
| `transcription_use_vocal_stem_by_default` | 1 | Default Transcribe source = Vocal |
| `transcription_prompt_enabled` | 1 | Pass karaoke/song prompt to Whisper |
| `transcription_prompt_text` | song prompt | Editable prompt (secrets blocked) |
| `transcription_chunking_enabled` | 0 | Optional overlapping chunk mode |
| `transcription_chunk_seconds` | 45 | Chunk length |
| `transcription_chunk_overlap_seconds` | 3 | Chunk overlap |
| `transcription_force_language` | — | Optional ISO-639-1 override |

### Job diagnostics (`Audio Separation Job`)

| Field | Purpose |
| --- | --- |
| `transcription_word_count` | Word count after transcription |
| `transcription_segment_count` | Segment count |
| `transcription_detected_language` | Language Whisper detected |
| `transcription_first_segment_start` | Start time of first segment (seconds) |
| `transcription_bad_timestamp_count` | Words with zero/invalid duration |
| `transcription_quality_warning` | User-facing warning when unreliable |

### Quality detection rules

Diagnostics flag unreliable transcripts when **any** of these apply:

1. **Suspicious first segment gap** — first segment starts ≥ 20s, or ≥ 10% into songs ≥ 120s  
2. **Bad word timestamps** — word `end <= start` or duration &lt; 0.05s  
3. **Suspiciously short transcript** — fewer than ~8 words/minute for audio ≥ 60s  
4. **Low segment confidence** — `avg_logprob < -1.0` when present  
5. **Language mismatch** — requested `ms`/`en` differs from detected (e.g. `javanese` → `jv`)

**User-facing warning (stored + shown in UI):**

> Transcript may be incomplete or unreliable. Try setting the language, using Vocal source, or correcting lyrics manually.

### Real regression case covered

A **217-second vocal stem** with:

- detected language = `javanese`
- first segment at ~60s
- zero-duration word timestamps
- phonetic/unreliable text
- missing early song content

…is now flagged by Gate 10.1 diagnostics (`test_real_poor_217s_vocal_transcript_is_flagged`).

### Backend touch points

- `utils/transcription_quality.py` — prompt/language resolution, chunk merge, diagnostics
- `utils/transcription_assets.py` — enhanced `prepare_audio_for_whisper`
- `utils/ffmpeg_media.py` — preprocess, chunk extract
- `integrations/openai_transcription_client.py` — prompt, chunking, language
- `workers/transcription_worker.py` — diagnostics after transcription
- `api/separation.py` — settings payload, retry from Completed, prompt param
- `audio-vocal-remover/src/JobDetailPanel.jsx` — Transcribe tab UX

### Tests

- `tests/test_gate10_1_transcription_quality.py` — 18 tests including real 217s case
- `tests/test_whisper_input_check.py` — updated for preprocess-enabled default
- `tests/test_milestone8.py` — Completed transcription can retry

---

## Manual test checklist

1. Use a 3-minute song with clear vocal stem.
2. Confirm **Vocal** source.
3. Set language (`ms` or `en`).
4. Run transcription.
5. Check word count, detected language, first segment start, bad timestamp count, warning.
6. Toggle preprocessing on/off and compare.
7. Toggle chunking on/off and compare.
8. Correct final lyrics in **Edit Lyrics**.

---

## Gate 10.2 recommendation (backlog)

**Reference Lyrics Mode** (not implemented in Gate 10.1)

- User pastes correct lyrics
- App uses Whisper timing as rough anchors
- Generated result becomes a **manual transcript draft**
- Original Whisper transcript remains unchanged

This would address cases where Whisper wording is unreliable even when timing is partially usable.

---

## Deploy notes

```bash
bench --site YOUR_SITE migrate
cd apps/audio_stem/audio-vocal-remover && yarn build
bench restart
```
