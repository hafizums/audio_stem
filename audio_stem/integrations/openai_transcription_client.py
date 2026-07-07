# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.limits import get_settings


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
				}
			)
		else:
			segments.append(
				{
					"id": getattr(segment, "id", None),
					"start": flt(getattr(segment, "start", 0)),
					"end": flt(getattr(segment, "end", 0)),
					"text": (getattr(segment, "text", "") or "").strip(),
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


def transcribe_with_whisper(local_audio_path: str, language: str | None = None) -> dict:
	settings = get_settings()
	model = (settings.transcription_model or "whisper-1").strip() or "whisper-1"
	use_word_timestamps = bool(cint(settings.enable_word_timestamps))

	client = get_openai_client()
	kwargs = {
		"model": model,
		"response_format": "verbose_json",
	}
	if language:
		kwargs["language"] = language
	if use_word_timestamps:
		kwargs["timestamp_granularities"] = ["word", "segment"]

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
