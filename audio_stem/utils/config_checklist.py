# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.limits import get_settings


def _item(key: str, label: str, status: str, message: str) -> dict:
	return {"key": key, "label": label, "status": status, "message": message}


def get_configuration_checklist_data() -> list[dict]:
	settings = get_settings()
	items: list[dict] = []

	items.append(
		_item(
			"settings_exists",
			_("Audio Separation Settings"),
			"ok",
			_("Settings record is available."),
		)
	)

	if cint(settings.enabled):
		items.append(_item("enabled", _("Separation Enabled"), "ok", _("Audio separation is enabled.")))
	else:
		items.append(
			_item(
				"enabled",
				_("Separation Enabled"),
				"error",
				_("Audio separation is disabled. Users cannot start jobs."),
			)
		)

	api_key = (settings.get_password("wavespeed_api_key", raise_exception=False) or "").strip()
	if api_key:
		items.append(
			_item("wavespeed_api_key", _("WaveSpeed API Key"), "ok", _("WaveSpeed API key is configured."))
		)
	else:
		items.append(
			_item(
				"wavespeed_api_key",
				_("WaveSpeed API Key"),
				"error",
				_("WaveSpeed API key is missing."),
			)
		)

	max_mb = cint(settings.max_file_size_mb)
	if max_mb > 0:
		items.append(
			_item(
				"max_file_size_mb",
				_("Max File Size"),
				"ok",
				_("Maximum upload size is {0} MB.").format(max_mb),
			)
		)
	else:
		items.append(
			_item(
				"max_file_size_mb",
				_("Max File Size"),
				"warning",
				_("Maximum upload size is not configured."),
			)
		)

	max_duration = cint(settings.max_audio_duration_seconds)
	if max_duration > 0:
		items.append(
			_item(
				"max_audio_duration_seconds",
				_("Max Audio Duration"),
				"ok",
				_("Maximum duration is {0} seconds.").format(max_duration),
			)
		)
	else:
		items.append(
			_item(
				"max_audio_duration_seconds",
				_("Max Audio Duration"),
				"warning",
				_("Maximum duration is not configured."),
			)
		)

	if cint(settings.cleanup_enabled):
		items.append(
			_item(
				"cleanup_settings",
				_("Cleanup Settings"),
				"ok",
				_("Cleanup is enabled with {0} day retention.").format(cint(settings.retention_days) or 7),
			)
		)
	else:
		items.append(
			_item(
				"cleanup_settings",
				_("Cleanup Settings"),
				"warning",
				_("Scheduled cleanup is disabled."),
			)
		)

	if cint(settings.credit_management_enabled):
		from audio_stem.integrations.credit_management_client import credit_management_available

		if credit_management_available():
			items.append(
				_item(
					"credit_integration",
					_("Credit Integration"),
					"ok",
					_("Credit integration is enabled and credit_management is available."),
				)
			)
		else:
			items.append(
				_item(
					"credit_integration",
					_("Credit Integration"),
					"error",
					_("Credit integration is enabled but credit_management is not installed."),
				)
			)
	else:
		items.append(
			_item(
				"credit_integration",
				_("Credit Integration"),
				"ok",
				_("Credit integration is disabled."),
			)
		)

	if _scheduler_hook_available():
		items.append(
			_item(
				"scheduler_hook",
				_("Cleanup Scheduler"),
				"ok",
				_("Daily cleanup scheduler hook is registered."),
			)
		)
	else:
		items.append(
			_item(
				"scheduler_hook",
				_("Cleanup Scheduler"),
				"warning",
				_("Daily cleanup scheduler hook was not found."),
			)
		)

	if cint(settings.store_outputs_locally):
		items.append(
			_item(
				"store_outputs_locally",
				_("Local Output Storage"),
				"ok",
				_("Completed outputs are stored as private Frappe files."),
			)
		)
	else:
		items.append(
			_item(
				"store_outputs_locally",
				_("Local Output Storage"),
				"warning",
				_("Outputs rely on provider URLs until local storage is enabled."),
			)
		)

	if cint(settings.notify_user_on_completion) or cint(settings.notify_user_on_failure):
		items.append(
			_item(
				"notifications",
				_("User Notifications"),
				"ok",
				_("Completion and/or failure notifications are enabled."),
			)
		)
	else:
		items.append(
			_item(
				"notifications",
				_("User Notifications"),
				"warning",
				_("User notifications are disabled."),
			)
		)

	if cint(settings.pilot_mode_enabled):
		items.append(
			_item(
				"pilot_mode",
				_("Pilot Mode"),
				"ok",
				_("Pilot mode is enabled."),
			)
		)
	else:
		items.append(
			_item(
				"pilot_mode",
				_("Pilot Mode"),
				"ok",
				_("Pilot mode is disabled."),
			)
		)

	from audio_stem.utils.provider_health import get_provider_health_summary

	provider_health = get_provider_health_summary()
	items.append(
		_item(
			"provider_health",
			_("Provider Health"),
			provider_health.get("status", "unknown"),
			provider_health.get("message"),
		)
	)

	from audio_stem.integrations.elevenlabs_scribe_client import is_elevenlabs_scribe_enabled, resolve_scribe_model
	from audio_stem.integrations.openai_transcription_client import is_openai_transcription_enabled
	from audio_stem.integrations.transcription_provider import (
		PROVIDER_ELEVENLABS,
		PROVIDER_OPENAI,
		get_transcription_provider,
		is_transcription_provider_configured,
	)
	from audio_stem.utils.scribe_keyterms import parse_keyterms, validate_keyterms

	selected_provider = get_transcription_provider(settings)
	items.append(
		_item(
			"transcription_provider",
			_("Transcription Provider"),
			"ok" if selected_provider in (PROVIDER_OPENAI, PROVIDER_ELEVENLABS) else "warning",
			_("Site transcription provider: {0}.").format(selected_provider),
		)
	)

	if selected_provider == PROVIDER_OPENAI:
		if is_transcription_provider_configured(PROVIDER_OPENAI, settings):
			items.append(_item("openai_api_key", _("OpenAI API Key"), "ok", _("OpenAI API key is configured.")))
			items.append(
				_item(
					"openai_enabled",
					_("OpenAI Transcription"),
					"ok",
					_("OpenAI transcription is enabled (model: {0}).").format(settings.transcription_model or "whisper-1"),
				)
			)
		else:
			openai_key = (settings.get_password("openai_api_key", raise_exception=False) or "").strip()
			if not openai_key:
				items.append(_item("openai_api_key", _("OpenAI API Key"), "error", _("OpenAI API key is missing.")))
			items.append(
				_item("openai_enabled", _("OpenAI Transcription"), "error", _("OpenAI transcription is not ready."))
			)
	elif selected_provider == PROVIDER_ELEVENLABS:
		if is_transcription_provider_configured(PROVIDER_ELEVENLABS, settings):
			items.append(
				_item("elevenlabs_api_key", _("ElevenLabs API Key"), "ok", _("ElevenLabs API key is configured."))
			)
			model = resolve_scribe_model(settings.elevenlabs_scribe_model, settings)
			items.append(
				_item(
					"elevenlabs_scribe_enabled",
					_("ElevenLabs Scribe"),
					"ok",
					_("ElevenLabs Scribe is enabled (model: {0}).").format(model),
				)
			)
			if cint(settings.elevenlabs_use_keyterms):
				try:
					validate_keyterms(parse_keyterms(settings.elevenlabs_keyterms))
					items.append(_item("elevenlabs_keyterms", _("ElevenLabs Keyterms"), "ok", _("Keyterms are valid.")))
				except Exception as exc:
					items.append(_item("elevenlabs_keyterms", _("ElevenLabs Keyterms"), "error", str(exc)))
		else:
			elevenlabs_key = (settings.get_password("elevenlabs_api_key", raise_exception=False) or "").strip()
			if not elevenlabs_key:
				items.append(
					_item("elevenlabs_api_key", _("ElevenLabs API Key"), "error", _("ElevenLabs API key is missing."))
				)
			items.append(
				_item(
					"elevenlabs_scribe_enabled",
					_("ElevenLabs Scribe"),
					"error",
					_("ElevenLabs Scribe is not ready."),
				)
			)

	if is_openai_transcription_enabled() or is_elevenlabs_scribe_enabled():
		items.append(
			_item(
				"word_timestamps",
				_("Word Timestamps"),
				"ok" if cint(settings.enable_word_timestamps) else "warning",
				_("Word timestamps are enabled.") if cint(settings.enable_word_timestamps) else _("Word timestamps are disabled."),
			)
		)
		items.append(
			_item(
				"manual_transcript_correction",
				_("Manual Transcript Correction"),
				"ok",
				_("Manual transcript correction is available after transcription."),
			)
		)
		max_words = cint(settings.subtitle_max_words_per_line) or cint(settings.karaoke_max_words_per_line)
		if max_words > 0 and flt(settings.subtitle_max_line_duration_seconds) > 0:
			items.append(
				_item(
					"subtitle_line_settings",
					_("Subtitle Line Settings"),
					"ok",
					_("Subtitle line settings are configured."),
				)
			)
		else:
			items.append(
				_item(
					"subtitle_line_settings",
					_("Subtitle Line Settings"),
					"warning",
					_("Subtitle line settings need review."),
				)
			)
	else:
		items.append(
			_item("transcription_enabled", _("Transcription"), "warning", _("No transcription provider is enabled."))
		)

	if cint(settings.karaoke_enabled):
		from audio_stem.utils.ffmpeg_media import is_ffmpeg_available, is_ffprobe_available
		from audio_stem.utils.karaoke_subtitles import is_karaoke_engine_available

		items.append(
			_item(
				"karaoke_enabled",
				_("Karaoke Subtitles"),
				"ok",
				_("Karaoke subtitle generation is enabled."),
			)
		)
		items.append(
			_item(
				"karaoke_ass_enabled",
				_("ASS Generation"),
				"ok" if cint(settings.karaoke_ass_enabled) else "warning",
				_("ASS subtitle generation is enabled.")
				if cint(settings.karaoke_ass_enabled)
				else _("ASS subtitle generation is disabled."),
			)
		)
		items.append(
			_item(
				"karaoke_video_render_enabled",
				_("Video Render"),
				"ok" if cint(settings.karaoke_video_render_enabled) else "warning",
				_("Karaoke MP4 rendering is enabled.")
				if cint(settings.karaoke_video_render_enabled)
				else _("Karaoke MP4 rendering is disabled (ASS only)."),
			)
		)
		items.append(
			_item(
				"karaoke_engine_available",
				_("karaoke_engine"),
				"ok" if is_karaoke_engine_available() else "error",
				_("karaoke_engine import is available.")
				if is_karaoke_engine_available()
				else _("Install karaoke_engine (editable: apps/karaoke_engine)."),
			)
		)
		if cint(settings.karaoke_video_render_enabled):
			items.append(
				_item(
					"ffmpeg_available",
					_("ffmpeg"),
					"ok" if is_ffmpeg_available() else "error",
					_("ffmpeg is available.") if is_ffmpeg_available() else _("ffmpeg is required for karaoke video rendering."),
				)
			)
			items.append(
				_item(
					"ffprobe_available",
					_("ffprobe"),
					"ok" if is_ffprobe_available() else "error",
					_("ffprobe is available.")
					if is_ffprobe_available()
					else _("ffprobe is required for karaoke video rendering."),
				)
			)
			if settings.get("default_karaoke_background_video"):
				items.append(
					_item(
						"default_karaoke_background_video",
						_("Default Karaoke Background Video"),
						"ok",
						_("A default karaoke background video is configured."),
					)
				)
			else:
				items.append(
					_item(
						"default_karaoke_background_video",
						_("Default Karaoke Background Video"),
						"warning",
						_("No default karaoke background video is configured. Jobs fall back to generated color."),
					)
				)
			items.append(
				_item(
					"allow_user_karaoke_background_upload",
					_("User Karaoke Background Upload"),
					"ok" if cint(settings.allow_user_karaoke_background_upload) else "warning",
					_("Users may upload job background videos.")
					if cint(settings.allow_user_karaoke_background_upload)
					else _("Only System Managers or the settings default background may be used."),
				)
			)
			items.append(
				_item(
					"karaoke_background_fit_mode",
					_("Karaoke Background Fit Mode"),
					"ok",
					_("Background fit mode is {0}.").format(settings.karaoke_background_fit_mode or "Cover"),
				)
			)
		items.append(
			_item(
				"pycaps_absent",
				_("PyCaps / Playwright"),
				"ok",
				_("PyCaps and Playwright are not used by audio_stem."),
			)
		)
	else:
		items.append(_item("karaoke_enabled", _("Karaoke Subtitles"), "warning", _("Karaoke subtitle generation is disabled.")))

	return items


def _scheduler_hook_available() -> bool:
	try:
		import audio_stem.hooks as hooks

		daily_jobs = hooks.scheduler_events.get("daily") or []
		return "audio_stem.utils.cleanup.cleanup_old_audio_jobs" in daily_jobs
	except Exception:
		return False
