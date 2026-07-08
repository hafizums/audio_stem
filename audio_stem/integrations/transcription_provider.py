# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Transcription provider abstraction for OpenAI Whisper and ElevenLabs Scribe."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

from audio_stem.integrations.elevenlabs_scribe_client import (
	is_elevenlabs_scribe_enabled,
	resolve_scribe_model,
	transcribe_with_scribe,
)
from audio_stem.integrations.openai_transcription_client import (
	is_openai_transcription_enabled,
	transcribe_with_whisper,
)
from audio_stem.utils.limits import get_settings
from audio_stem.utils.scribe_keyterms import parse_keyterms, validate_keyterms

PROVIDER_OPENAI = "OpenAI Whisper"
PROVIDER_ELEVENLABS = "ElevenLabs Scribe"
VALID_PROVIDERS = frozenset({PROVIDER_OPENAI, PROVIDER_ELEVENLABS})


def get_transcription_provider(settings=None) -> str:
	settings = settings or get_settings()
	provider = (settings.transcription_provider or PROVIDER_OPENAI).strip()
	if provider not in VALID_PROVIDERS:
		return PROVIDER_OPENAI
	return provider


def resolve_transcription_provider(provider: str | None = None, settings=None) -> str:
	settings = settings or get_settings()
	provider = (provider or get_transcription_provider(settings)).strip()
	if provider not in VALID_PROVIDERS:
		frappe.throw(_("Invalid transcription provider."), frappe.ValidationError)
	return provider


def is_transcription_provider_configured(provider: str | None = None, settings=None) -> bool:
	settings = settings or get_settings()
	provider = resolve_transcription_provider(provider, settings)
	if provider == PROVIDER_OPENAI:
		if not is_openai_transcription_enabled():
			return False
		api_key = (settings.get_password("openai_api_key", raise_exception=False) or "").strip()
		return bool(api_key)
	if provider == PROVIDER_ELEVENLABS:
		if not is_elevenlabs_scribe_enabled():
			return False
		api_key = (settings.get_password("elevenlabs_api_key", raise_exception=False) or "").strip()
		return bool(api_key)
	return False


def get_transcription_provider_blocked_reason(provider: str | None = None, settings=None) -> str | None:
	settings = settings or get_settings()
	provider = resolve_transcription_provider(provider, settings)
	if provider == PROVIDER_OPENAI:
		if not is_openai_transcription_enabled():
			return _("OpenAI transcription is disabled.")
		if not (settings.get_password("openai_api_key", raise_exception=False) or "").strip():
			return _("OpenAI API key is not configured.")
	elif provider == PROVIDER_ELEVENLABS:
		if not is_elevenlabs_scribe_enabled():
			return _("ElevenLabs Scribe is disabled.")
		if not (settings.get_password("elevenlabs_api_key", raise_exception=False) or "").strip():
			return _("ElevenLabs API key is not configured.")
	return None


def normalize_transcript_result(provider: str, provider_result: dict) -> dict:
	settings = get_settings()
	result = dict(provider_result or {})
	result["provider"] = provider
	if provider == PROVIDER_OPENAI:
		result["model"] = (settings.transcription_model or "whisper-1").strip() or "whisper-1"
	elif provider == PROVIDER_ELEVENLABS:
		result["model"] = result.get("model") or resolve_scribe_model(settings=settings)
	return result


def transcribe_audio(
	local_audio_path: str,
	language: str | None = None,
	prompt: str | None = None,
	keyterms: list[str] | None = None,
	*,
	provider: str | None = None,
	scribe_model: str | None = None,
	no_verbatim: bool | None = None,
	tag_audio_events: bool | None = None,
	diarize: bool | None = None,
	temperature: float | None = None,
) -> dict:
	settings = get_settings()
	provider = resolve_transcription_provider(provider, settings)
	blocked_reason = get_transcription_provider_blocked_reason(provider, settings)
	if blocked_reason:
		frappe.throw(blocked_reason, frappe.ValidationError)

	if provider == PROVIDER_OPENAI:
		result = transcribe_with_whisper(local_audio_path, language=language, prompt=prompt)
	elif provider == PROVIDER_ELEVENLABS:
		parsed_keyterms = list(keyterms or [])
		if not parsed_keyterms and cint(settings.elevenlabs_use_keyterms):
			parsed_keyterms = parse_keyterms(settings.elevenlabs_keyterms)
		validate_keyterms(parsed_keyterms)
		result = transcribe_with_scribe(
			local_audio_path,
			language=language,
			keyterms=parsed_keyterms,
			model=scribe_model,
			no_verbatim=no_verbatim,
			tag_audio_events=tag_audio_events,
			diarize=diarize,
			temperature=temperature,
		)
	else:
		frappe.throw(_("Unsupported transcription provider."), frappe.ValidationError)

	return normalize_transcript_result(provider, result)
