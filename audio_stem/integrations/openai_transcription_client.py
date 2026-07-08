# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.ffmpeg_media import extract_audio_segment, probe_media_duration
from audio_stem.utils.limits import get_settings
from audio_stem.utils.transcription_quality import (
	build_chunk_continuation_prompt,
	build_whisper_style_primer,
	merge_chunk_transcripts,
	offset_transcript_timestamps,
	resolve_transcription_language,
)


def is_openai_transcription_enabled() -> bool:
	settings = get_settings()
	return bool(cint(settings.openai_enabled))


def get_openai_client():
	settings = get_settings()
	if not cint(settings.openai_enabled):
		frappe.throw(_("OpenAI transcription is disabled in Audio Separation Settings."), frappe.ValidationError)

	api_key = (settings.get_password("openai_api_key", raise_exception=False) or "").strip()
	if not api_key:
		frappe.throw(
			_("OpenAI API key is not configured in Audio Separation Settings."),
			frappe.ValidationError,
		)

	from openai import OpenAI

	return OpenAI(api_key=api_key)


def _normalize_transcript_response(response, raw_response_dict: dict) -> dict:
	text = getattr(response, "text", None) or raw_response_dict.get("text") or ""
	language = getattr(response, "language", None) or raw_response_dict.get("language")
	duration = flt(getattr(response, "duration", None) or raw_response_dict.get("duration"))

	segments = []
	for segment in raw_response_dict.get("segments") or getattr(response, "segments", None) or []:
		if isinstance(segment, dict):
			segments.append(
				{
					"id": segment.get("id"),
					"start": flt(segment.get("start")),
					"end": flt(segment.get("end")),
					"text": (segment.get("text") or "").strip(),
					"avg_logprob": segment.get("avg_logprob"),
				}
			)
		else:
			segments.append(
				{
					"id": getattr(segment, "id", None),
					"start": flt(getattr(segment, "start", 0)),
					"end": flt(getattr(segment, "end", 0)),
					"text": (getattr(segment, "text", "") or "").strip(),
					"avg_logprob": getattr(segment, "avg_logprob", None),
				}
			)

	words = []
	for word in raw_response_dict.get("words") or getattr(response, "words", None) or []:
		if isinstance(word, dict):
			words.append(
				{
					"word": (word.get("word") or word.get("text") or "").strip(),
					"start": flt(word.get("start")),
					"end": flt(word.get("end")),
				}
			)
		else:
			words.append(
				{
					"word": (getattr(word, "word", None) or getattr(word, "text", "") or "").strip(),
					"start": flt(getattr(word, "start", 0)),
					"end": flt(getattr(word, "end", 0)),
				}
			)

	return {
		"text": text.strip(),
		"language": language,
		"duration": duration,
		"segments": segments,
		"words": words,
		"raw_response_dict": raw_response_dict,
	}


def _build_whisper_request_kwargs(*, language: str | None, prompt: str | None) -> dict:
	settings = get_settings()
	model = (settings.transcription_model or "whisper-1").strip() or "whisper-1"
	use_word_timestamps = bool(cint(settings.enable_word_timestamps))

	kwargs = {
		"model": model,
		"response_format": "verbose_json",
	}
	if language:
		kwargs["language"] = language
	if prompt:
		kwargs["prompt"] = prompt
	if use_word_timestamps:
		kwargs["timestamp_granularities"] = ["word", "segment"]
	return kwargs


def _transcribe_single_file(
	local_audio_path: str,
	*,
	language: str | None = None,
	prompt: str | None = None,
) -> dict:
	client = get_openai_client()
	kwargs = _build_whisper_request_kwargs(language=language, prompt=prompt)

	try:
		with open(local_audio_path, "rb") as audio_file:
			response = client.audio.transcriptions.create(file=audio_file, **kwargs)
	except Exception as exc:
		frappe.log_error(title="OpenAI transcription failed", message=frappe.get_traceback())
		frappe.throw(safe_error_message(exc), frappe.ValidationError)

	if hasattr(response, "model_dump"):
		raw_response_dict = response.model_dump()
	elif isinstance(response, dict):
		raw_response_dict = response
	else:
		raw_response_dict = {
			"text": getattr(response, "text", ""),
			"language": getattr(response, "language", None),
			"duration": getattr(response, "duration", None),
			"segments": getattr(response, "segments", None),
			"words": getattr(response, "words", None),
		}

	normalized = _normalize_transcript_response(response, raw_response_dict)
	if not normalized.get("text") and not normalized.get("segments"):
		frappe.throw(_("Transcription returned no text."), frappe.ValidationError)
	return normalized


def _split_audio_for_chunking(local_audio_path: str) -> list[tuple[str, float, float, bool]]:
	settings = get_settings()
	chunk_seconds = max(cint(settings.transcription_chunk_seconds) or 45, 10)
	overlap_seconds = max(cint(settings.transcription_chunk_overlap_seconds) or 3, 0)
	overlap_seconds = min(overlap_seconds, max(chunk_seconds // 2, 1))

	duration = probe_media_duration(local_audio_path) or 0
	if duration <= chunk_seconds:
		return [(local_audio_path, 0.0, duration, False)]

	sample_rate = cint(settings.transcription_preprocess_sample_rate) or 16000
	channels = cint(settings.transcription_preprocess_channels) or 1
	bitrate = (settings.transcription_preprocess_bitrate or "64k").strip() or "64k"

	chunks: list[tuple[str, float, float, bool]] = []
	step = max(chunk_seconds - overlap_seconds, 1)
	start = 0.0
	while start < duration:
		segment_duration = min(chunk_seconds, duration - start)
		if segment_duration <= 0:
			break
		chunk_path = extract_audio_segment(
			local_audio_path,
			start_seconds=start,
			duration_seconds=segment_duration,
			sample_rate=sample_rate,
			channels=channels,
			bitrate=bitrate,
		)
		chunks.append((chunk_path, start, segment_duration, True))
		if start + segment_duration >= duration:
			break
		start += step
	return chunks


def _transcribe_with_chunking(
	local_audio_path: str,
	*,
	language: str | None = None,
	style_primer: str | None = None,
) -> dict:
	chunk_specs = _split_audio_for_chunking(local_audio_path)
	merged_chunks: list[dict] = []
	previous_text = ""

	try:
		for chunk_path, offset_seconds, chunk_duration, should_cleanup in chunk_specs:
			chunk_prompt = build_chunk_continuation_prompt(
				previous_chunk_text=previous_text or None,
				style_primer=style_primer if not previous_text else None,
			)
			chunk_result = _transcribe_single_file(
				chunk_path,
				language=language,
				prompt=chunk_prompt,
			)
			if chunk_duration and not chunk_result.get("duration"):
				chunk_result["duration"] = chunk_duration
			merged_chunks.append(offset_transcript_timestamps(chunk_result, offset_seconds))
			previous_text = (chunk_result.get("text") or "").strip()
	finally:
		from audio_stem.utils.transcription_assets import cleanup_temp_path

		for chunk_path, _, _, should_cleanup in chunk_specs:
			cleanup_temp_path(chunk_path, should_cleanup=should_cleanup)

	return merge_chunk_transcripts(merged_chunks)


def transcribe_with_whisper(
	local_audio_path: str,
	language: str | None = None,
	prompt: str | None = None,
) -> dict:
	settings = get_settings()
	resolved_language = resolve_transcription_language(language, settings)
	style_primer = build_whisper_style_primer(settings, user_primer=prompt)

	if cint(settings.transcription_chunking_enabled):
		return _transcribe_with_chunking(
			local_audio_path,
			language=resolved_language,
			style_primer=style_primer,
		)

	return _transcribe_single_file(
		local_audio_path,
		language=resolved_language,
		prompt=style_primer,
	)
