# Gate 10.3 — Multi-Provider Transcription

**Status:** Implemented  
**Date:** 2026-07-08

## Summary

Adds **ElevenLabs Scribe v1/v2** as an alternative transcription provider alongside **OpenAI whisper-1**, using a backend provider abstraction. Karaoke pipeline unchanged.

## Providers

| Provider | Client | Default model |
| --- | --- | --- |
| OpenAI Whisper | `openai_transcription_client.py` | `whisper-1` |
| ElevenLabs Scribe | `elevenlabs_scribe_client.py` | `scribe_v2` |

## Key files

- `integrations/transcription_provider.py` — `transcribe_audio()`, provider routing
- `integrations/elevenlabs_scribe_client.py` — Scribe API + normalization
- `utils/scribe_keyterms.py` — keyterm parse/validate
- `utils/scribe_segments.py` — `group_words_into_segments()`
- `workers/transcription_worker.py` — uses `transcribe_audio()`
- `api/separation.py` — provider params on `start_transcription`
- `audio-vocal-remover/src/JobDetailPanel.jsx` — provider selector UI

## Normalized transcript shape

```json
{
  "text": "...",
  "language": "msa",
  "duration": 217.0,
  "segments": [...],
  "words": [...],
  "provider": "ElevenLabs Scribe",
  "model": "scribe_v2",
  "raw_response_dict": {...}
}
```

## Tests

`test_gate10_3_transcription_providers.py` — settings, Scribe client, normalization, cost, worker, security.

## Deploy

```bash
bench --site jomveo migrate
cd apps/audio_stem/audio-vocal-remover && yarn build
bench restart
bench --site jomveo run-tests --app audio_stem
```
