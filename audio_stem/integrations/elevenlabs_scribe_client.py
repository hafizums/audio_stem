# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json
import os

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.ffmpeg_media import probe_media_duration
from audio_stem.utils.limits import get_settings
from audio_stem.utils.scribe_keyterms import parse_keyterms, validate_keyterms
from audio_stem.utils.scribe_segments import group_words_into_segments

ELEVENLABS_SPEECH_TO_TEXT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
VALID_SCRIBE_MODELS = frozenset({"scribe_v1", "scribe_v2"})


def is_elevenlabs_scribe_enabled() -> bool:
	settings = get_settings()
	return bool(cint(settings.elevenlabs_scribe_enabled))


def get_elevenlabs_api_key(settings=None) -> str:
	settings = settings or get_settings()
	return (settings.get_password("elevenlabs_api_key", raise_exception=False) or "").strip()


def resolve_scribe_model(model: str | None = None, settings=None) -> str:
	settings = settings or get_settings()
	model = (model or settings.elevenlabs_scribe_model or "scribe_v2").strip()
	if model not in VALID_SCRIBE_MODELS:
		frappe.throw(_("Invalid ElevenLabs Scribe model."), frappe.ValidationError)
	return model


def normalize_scribe_response(raw_response: dict, *, duration_fallback: float | None = None) -> dict:
	language = raw_response.get("language_code") or raw_response.get("language")
	language_probability = raw_response.get("language_probability")
	text = (raw_response.get("text") or "").strip()

	words = []
	for item in raw_response.get("words") or []:
		if not isinstance(item, dict):
			continue
		if (item.get("type") or "word") != "word":
			continue
		word_text = (item.get("text") or item.get("word") or "").strip()
		if not word_text:
			continue
		entry = {
			"word": word_text,
			"start": flt(item.get("start")),
			"end": flt(item.get("end")),
		}
		if item.get("logprob") is not None:
			entry["logprob"] = flt(item.get("logprob"))
		words.append(entry)

	segments = raw_response.get("segments") or []
	if not segments and words:
		segments = group_words_into_segments(words)

	duration = flt(raw_response.get("duration"))
	if not duration:
		duration = flt(duration_fallback or 0)

	return {
		"text": text,
		"language": language,
		"language_probability": language_probability,
		"duration": duration,
		"segments": segments,
		"words": words,
		"provider": "ElevenLabs Scribe",
		"model": raw_response.get("model_id"),
		"raw_response_dict": raw_response,
	}


def transcribe_with_scribe(
	local_audio_path: str,
	language: str | None = None,
	keyterms: list[str] | None = None,
	*,
	model: str | None = None,
	no_verbatim: bool | None = None,
	tag_audio_events: bool | None = None,
	diarize: bool | None = None,
	temperature: float | None = None,
) -> dict:
	settings = get_settings()
	if not is_elevenlabs_scribe_enabled():
		frappe.throw(_("ElevenLabs Scribe is disabled in Audio Separation Settings."), frappe.ValidationError)

	api_key = get_elevenlabs_api_key(settings)
	if not api_key:
		frappe.throw(
			_("ElevenLabs API key is not configured in Audio Separation Settings."),
			frappe.ValidationError,
		)

	model_id = resolve_scribe_model(model, settings)
	timeout = max(cint(settings.elevenlabs_timeout_seconds) or 900, 30)
	language_code = (language or settings.elevenlabs_language_code or "").strip() or None

	parsed_keyterms = list(keyterms or [])
	if not parsed_keyterms and cint(settings.elevenlabs_use_keyterms):
		parsed_keyterms = parse_keyterms(settings.elevenlabs_keyterms)
	validate_keyterms(parsed_keyterms)

	if no_verbatim is None:
		no_verbatim = bool(cint(settings.elevenlabs_no_verbatim))
	if tag_audio_events is None:
		tag_audio_events = bool(cint(settings.elevenlabs_tag_audio_events))
	if diarize is None:
		diarize = bool(cint(settings.elevenlabs_diarize))
	if temperature is None and settings.elevenlabs_temperature not in (None, ""):
		temperature = flt(settings.elevenlabs_temperature)

	form_data = {
		"model_id": model_id,
		"timestamps_granularity": "word",
		"tag_audio_events": "true" if tag_audio_events else "false",
		"diarize": "true" if diarize else "false",
	}
	if language_code:
		form_data["language_code"] = language_code
	if no_verbatim and model_id == "scribe_v2":
		form_data["no_verbatim"] = "true"
	if temperature is not None:
		form_data["temperature"] = str(temperature)
	if parsed_keyterms:
		form_data["keyterms"] = json.dumps(parsed_keyterms)

	headers = {"xi-api-key": api_key}
	filename = os.path.basename(local_audio_path) or "audio.mp3"

	try:
		with open(local_audio_path, "rb") as audio_file:
			response = requests.post(
				ELEVENLABS_SPEECH_TO_TEXT_URL,
				headers=headers,
				data=form_data,
				files={"file": (filename, audio_file)},
				timeout=timeout,
			)
	except requests.Timeout:
		frappe.log_error(title="ElevenLabs Scribe timed out", message=f"Timeout after {timeout}s")
		frappe.throw(_("Transcription timed out. Please try again later."), frappe.ValidationError)
	except requests.RequestException as exc:
		frappe.log_error(title="ElevenLabs Scribe request failed", message=frappe.get_traceback())
		frappe.throw(safe_error_message(exc), frappe.ValidationError)

	if response.status_code >= 400:
		frappe.log_error(
			title="ElevenLabs Scribe API error",
			message=response.text or f"HTTP {response.status_code}",
		)
		frappe.throw(_("Transcription failed. Please contact an administrator."), frappe.ValidationError)

	try:
		raw_response = response.json()
	except ValueError:
		frappe.log_error(title="ElevenLabs Scribe invalid JSON", message=response.text)
		frappe.throw(_("Transcription returned an invalid response."), frappe.ValidationError)

	raw_response["model_id"] = model_id
	duration_fallback = probe_media_duration(local_audio_path)
	normalized = normalize_scribe_response(raw_response, duration_fallback=duration_fallback)
	if parsed_keyterms:
		normalized["keyterms_used"] = parsed_keyterms
	if not normalized.get("text") and not normalized.get("segments"):
		frappe.throw(_("Transcription returned no text."), frappe.ValidationError)
	return normalized
