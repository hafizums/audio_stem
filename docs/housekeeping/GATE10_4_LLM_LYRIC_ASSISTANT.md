# Gate 10.4 — WaveSpeed LLM Lyric Assistant

**Status:** Implemented  
**Date:** 2026-07-08

## Summary

Optional backend-only LLM assistant via WaveSpeed LLM (`https://llm.wavespeed.ai/v1`). Helps repair lyrics, split lines, suggest Scribe keyterms, and explain transcription quality without replacing ASR or auto-approving manual transcripts.

## Key files

- `integrations/wavespeed_llm_client.py` — OpenAI SDK client
- `integrations/llm_provider.py` — task router
- `utils/lyric_assistant.py` — prompts, payloads, manual draft merge
- `utils/llm_assistant_controls.py` — enqueue guards
- `workers/llm_assistant_worker.py` — background processing
- `api/separation.py` — whitelisted APIs
- `audio-vocal-remover/src/TranscriptEditor.jsx` — AI Lyric Assistant card

## Tests

`test_gate10_4_llm_lyric_assistant.py`

## Deploy

```bash
bench --site jomveo migrate
cd apps/audio_stem/audio-vocal-remover && yarn build
bench restart
bench --site jomveo run-tests --app audio_stem
```
