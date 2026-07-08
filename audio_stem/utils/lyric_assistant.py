# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from audio_stem.integrations.llm_provider import (
	estimate_llm_cost_usd,
	normalize_llm_task_result,
	normalize_task_name,
	run_llm_json_task,
)
from audio_stem.utils.limits import get_settings
from audio_stem.utils.scribe_keyterms import parse_keyterms, validate_keyterms
from audio_stem.utils.transcription_assets import _attach_private_file
from audio_stem.utils.transcript_corrections import (
	_load_json_file,
	load_transcript_for_edit,
	save_manual_transcript,
	sync_segment_text_and_words,
)

SYSTEM_PROMPT_REPAIR = """You are a karaoke lyric assistant. You repair automatic speech recognition (ASR) transcript text for songs.

Rules:
- Do not translate unless explicitly asked.
- Do not invent missing lyrics.
- Preserve repeated chorus lines and filler when they appear in the ASR text.
- Preserve local-language words when uncertain; keep phonetic spellings rather than guessing.
- If unsure about a word, keep the closest ASR text and add a warning.
- Do not change timestamps except minor segment text alignment.
- Always set requires_manual_review to true.
- Return valid JSON only."""

SYSTEM_PROMPT_SPLIT = """You split song lyrics into karaoke-friendly lines.

Rules:
- Preserve original wording; do not translate unless asked.
- Do not invent lyrics.
- Keep chorus repetitions.
- Return suggested_segments with text only; timings may be approximate placeholders.
- Always set requires_manual_review to true.
- Return valid JSON only."""

SYSTEM_PROMPT_KEYTERMS = """You suggest Scribe keyterms for song transcription.

Rules:
- Return only terms useful for speech-to-text keyterm prompting.
- Prefer local-language words, names, places, chorus phrases, and repeated song-specific terms.
- Do not include secrets or full sentences.
- Maximum 100 keyterms; each under 50 characters and at most 5 words.
- No characters: < > { } [ ] backslash
- Return valid JSON only."""

SYSTEM_PROMPT_EXPLAIN = """You explain transcription quality issues for karaoke preparation.

Rules:
- Be concise and practical.
- Do not invent lyrics.
- Suggest next steps for the user (language code, keyterms, manual correction).
- Always set requires_manual_review to true.
- Return valid JSON only."""


def _load_original_asr_transcript(job) -> dict:
	if not job.transcript_json_file:
		frappe.throw(_("Original transcript is not available."), frappe.ValidationError)
	return _load_json_file(job.transcript_json_file)


def _truncate_text(text: str, max_chars: int) -> str:
	value = (text or "").strip()
	if len(value) <= max_chars:
		return value
	return value[: max_chars - 3] + "..."


def _compact_segments(segments: list, *, limit: int = 80) -> list[dict]:
	compact = []
	for segment in (segments or [])[:limit]:
		if not isinstance(segment, dict):
			continue
		compact.append(
			{
				"text": (segment.get("text") or "").strip(),
				"start": flt(segment.get("start")),
				"end": flt(segment.get("end")),
			}
		)
	return compact


def build_transcript_repair_payload(job, options: dict | None = None) -> dict:
	options = options or {}
	original = _load_original_asr_transcript(job)
	settings = get_settings()
	max_chars = cint(settings.wavespeed_llm_max_input_chars) or 12000

	payload = {
		"transcript_text": _truncate_text(original.get("text") or job.transcript_text, max_chars),
		"segments": _compact_segments(original.get("segments") or []),
		"detected_language": job.transcription_detected_language or original.get("language"),
		"language_hint": options.get("language_hint") or job.transcription_language,
		"reference_lyrics": _truncate_text(options.get("reference_lyrics") or "", max_chars // 4),
		"quality_warnings": [
			warning
			for warning in [
				job.transcription_quality_warning,
				job.transcription_provider_warning,
			]
			if warning
		],
		"duration_seconds": flt(job.duration_seconds),
	}
	return payload


def build_llm_messages(task_name: str, payload: dict, settings=None) -> list[dict]:
	task = normalize_task_name(task_name)
	user_content = json.dumps(payload, ensure_ascii=False)

	if task == "repair_transcript_text":
		system_prompt = SYSTEM_PROMPT_REPAIR
		schema = {
			"suggested_text": "full repaired transcript",
			"suggested_segments": [{"text": "line", "start": 0.0, "end": 0.0}],
			"warnings": [],
			"confidence_notes": [],
			"requires_manual_review": True,
		}
	elif task == "split_lyrics_lines":
		system_prompt = SYSTEM_PROMPT_SPLIT
		schema = {
			"suggested_text": "full lyrics",
			"suggested_segments": [{"text": "karaoke line"}],
			"warnings": [],
			"confidence_notes": [],
			"requires_manual_review": True,
		}
	elif task == "suggest_scribe_keyterms":
		system_prompt = SYSTEM_PROMPT_KEYTERMS
		schema = {"keyterms": ["term1", "term2"], "warnings": [], "confidence_notes": [], "requires_manual_review": True}
	elif task == "explain_transcription_quality":
		system_prompt = SYSTEM_PROMPT_EXPLAIN
		schema = {
			"summary": "quality explanation",
			"warnings": [],
			"confidence_notes": [],
			"requires_manual_review": True,
		}
	else:
		frappe.throw(_("Unsupported LLM task: {0}").format(task_name), frappe.ValidationError)

	return [
		{"role": "system", "content": system_prompt},
		{
			"role": "user",
			"content": (
				f"Task: {task}\n"
				f"Return JSON matching this schema shape:\n{json.dumps(schema, ensure_ascii=False)}\n"
				f"Input:\n{user_content}"
			),
		},
	]


def _write_llm_suggestion_json(job, suggestion: dict) -> str:
	content = json.dumps(suggestion, indent=2, ensure_ascii=False)
	file_url = _attach_private_file(
		job,
		file_name=f"{job.name}-llm-suggestion.json",
		content=content,
		fieldname="llm_suggestion_json_file",
	)
	job.llm_suggestion_json_file = file_url
	return file_url


def _apply_llm_usage_fields(job, suggestion: dict, settings=None):
	settings = settings or get_settings()
	job.llm_input_tokens = cint(suggestion.get("input_tokens"))
	job.llm_output_tokens = cint(suggestion.get("output_tokens"))
	job.llm_estimated_cost_usd = estimate_llm_cost_usd(
		job.llm_input_tokens,
		job.llm_output_tokens,
		settings=settings,
	)


def save_llm_suggestion(job, suggestion: dict, *, status: str = "Completed") -> dict:
	suggestion = dict(suggestion or {})
	job.llm_suggestion_status = status
	job.llm_suggestion_error = None
	job.llm_suggestion_provider = suggestion.get("provider")
	job.llm_suggestion_model = suggestion.get("model")
	job.llm_suggestion_task = suggestion.get("task")
	job.llm_suggested_transcript_text = suggestion.get("suggested_text")
	job.llm_suggestion_updated_at = now_datetime()
	_apply_llm_usage_fields(job, suggestion)
	_write_llm_suggestion_json(job, suggestion)
	job.save(ignore_permissions=True)
	return get_llm_suggestion_payload(job)


def fail_llm_suggestion(job, error_message: str) -> None:
	job.llm_suggestion_status = "Failed"
	job.llm_suggestion_error = error_message
	job.llm_suggestion_updated_at = now_datetime()
	job.save(ignore_permissions=True)


def get_llm_suggestion_payload(job) -> dict:
	suggestion = None
	if job.llm_suggestion_json_file:
		try:
			suggestion = _load_json_file(job.llm_suggestion_json_file)
		except Exception:
			suggestion = None

	return {
		"llm_suggestion_status": job.llm_suggestion_status or "Not Started",
		"llm_suggested_transcript_text": job.llm_suggested_transcript_text,
		"llm_suggestion_error": job.llm_suggestion_error,
		"llm_suggestion_provider": job.llm_suggestion_provider,
		"llm_suggestion_model": job.llm_suggestion_model,
		"llm_suggestion_task": job.llm_suggestion_task,
		"llm_suggestion_updated_at": job.llm_suggestion_updated_at,
		"llm_input_tokens": cint(job.llm_input_tokens),
		"llm_output_tokens": cint(job.llm_output_tokens),
		"llm_estimated_cost_usd": flt(job.llm_estimated_cost_usd),
		"suggestion": suggestion,
	}


def suggest_transcript_corrections(job, options: dict | None = None) -> dict:
	payload = build_transcript_repair_payload(job, options=options)
	result = run_llm_json_task("repair_transcript_text", payload)
	save_llm_suggestion(job, result)
	return get_llm_suggestion_payload(job)


def suggest_keyterms_from_lyrics(lyrics_text: str, language_hint: str | None = None) -> list[str]:
	text = (lyrics_text or "").strip()
	if not text:
		frappe.throw(_("Lyrics text is required for keyterm suggestions."), frappe.ValidationError)

	result = run_llm_json_task(
		"suggest_scribe_keyterms",
		{"lyrics_text": text, "language_hint": language_hint},
	)
	terms = result.get("keyterms") or []
	parsed = parse_keyterms("\n".join(terms)) if terms else []
	validate_keyterms(parsed)
	return parsed


def split_reference_lyrics_for_karaoke(lyrics_text: str, language_hint: str | None = None) -> dict:
	text = (lyrics_text or "").strip()
	if not text:
		frappe.throw(_("Lyrics text is required."), frappe.ValidationError)

	return run_llm_json_task(
		"split_lyrics_lines",
		{"lyrics_text": text, "language_hint": language_hint},
	)


def explain_transcription_quality(job) -> dict:
	payload = build_transcript_repair_payload(job)
	payload["quality_summary_request"] = True
	result = run_llm_json_task("explain_transcription_quality", payload, use_reasoning_model=True)
	save_llm_suggestion(job, result)
	return get_llm_suggestion_payload(job)


def create_manual_draft_from_llm_suggestion(job, suggestion: dict | None = None) -> dict:
	if suggestion is None:
		if not job.llm_suggestion_json_file:
			frappe.throw(_("No LLM suggestion is available to accept."), frappe.ValidationError)
		suggestion = _load_json_file(job.llm_suggestion_json_file)

	task = normalize_task_name(suggestion.get("task") or job.llm_suggestion_task or "")
	if task == "suggest_scribe_keyterms":
		frappe.throw(_("Keyterm suggestions cannot be accepted as a manual transcript draft."), frappe.ValidationError)
	if task == "explain_transcription_quality":
		frappe.throw(_("Quality explanations cannot be accepted as a manual transcript draft."), frappe.ValidationError)

	original = _load_original_asr_transcript(job)
	original_segments = list(original.get("segments") or [])
	suggested_segments = list(suggestion.get("suggested_segments") or [])

	merged_segments = []
	for index, original_segment in enumerate(original_segments):
		segment = dict(original_segment)
		if index < len(suggested_segments):
			suggested = suggested_segments[index]
			if isinstance(suggested, dict) and (suggested.get("text") or "").strip():
				segment["text"] = (suggested.get("text") or "").strip()
		merged_segments.append(
			sync_segment_text_and_words(segment, min_word_duration=flt(get_settings().subtitle_min_word_duration_seconds) or 0.08)
		)

	if not merged_segments and suggested_segments:
		for suggested in suggested_segments:
			if not isinstance(suggested, dict):
				continue
			text = (suggested.get("text") or "").strip()
			if not text:
				continue
			merged_segments.append(
				{
					"text": text,
					"start": flt(suggested.get("start")),
					"end": flt(suggested.get("end") or suggested.get("start")),
					"words": [],
				}
			)

	suggested_text = (suggestion.get("suggested_text") or "").strip()
	if not suggested_text and merged_segments:
		suggested_text = " ".join(segment.get("text") or "" for segment in merged_segments).strip()

	draft_payload = {
		"text": suggested_text,
		"language": original.get("language") or job.transcription_detected_language,
		"duration": original.get("duration") or flt(job.duration_seconds),
		"segments": merged_segments,
		"words": [],
	}

	result = save_manual_transcript(job, draft_payload, status="Draft")
	original_after = _load_original_asr_transcript(job)
	if original_after.get("text") != original.get("text"):
		frappe.throw(_("Original ASR transcript was modified unexpectedly."), frappe.ValidationError)

	return {
		**result,
		**get_llm_suggestion_payload(job),
		"manual_transcript_status": job.manual_transcript_status,
	}
