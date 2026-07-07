# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import cint

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

	if cint(settings.openai_enabled):
		openai_key = (settings.get_password("openai_api_key", raise_exception=False) or "").strip()
		if openai_key:
			items.append(_item("openai_api_key", _("OpenAI API Key"), "ok", _("OpenAI API key is configured.")))
		else:
			items.append(_item("openai_api_key", _("OpenAI API Key"), "error", _("OpenAI API key is missing.")))
		items.append(
			_item(
				"openai_enabled",
				_("OpenAI Transcription"),
				"ok",
				_("OpenAI transcription is enabled (model: {0}).").format(settings.transcription_model or "whisper-1"),
			)
		)
		items.append(
			_item(
				"word_timestamps",
				_("Word Timestamps"),
				"ok" if cint(settings.enable_word_timestamps) else "warning",
				_("Word timestamps are enabled.") if cint(settings.enable_word_timestamps) else _("Word timestamps are disabled."),
			)
		)
	else:
		items.append(
			_item("openai_enabled", _("OpenAI Transcription"), "warning", _("OpenAI transcription is disabled."))
		)

	if cint(settings.karaoke_enabled):
		from audio_stem.utils.ffmpeg_media import is_ffmpeg_available

		items.append(
			_item(
				"karaoke_enabled",
				_("Karaoke Rendering"),
				"ok",
				_("Karaoke is enabled (template: {0}).").format(settings.karaoke_default_template or "hype"),
			)
		)
		items.append(
			_item(
				"ffmpeg_available",
				_("ffmpeg"),
				"ok" if is_ffmpeg_available() else "error",
				_("ffmpeg is available.") if is_ffmpeg_available() else _("ffmpeg is required for karaoke video generation."),
			)
		)
		from audio_stem.utils.karaoke_subtitles import is_pycaps_available

		items.append(
			_item(
				"pycaps_available",
				_("PyCaps (pycaps-ai)"),
				"ok" if is_pycaps_available() else "error",
				_("PyCaps subtitle renderer is available.")
				if is_pycaps_available()
				else _("Install pycaps-ai (not the unrelated PyPI package pycaps) and run playwright install chromium."),
			)
		)
	else:
		items.append(_item("karaoke_enabled", _("Karaoke Rendering"), "warning", _("Karaoke rendering is disabled.")))

	return items


def _scheduler_hook_available() -> bool:
	try:
		import audio_stem.hooks as hooks

		daily_jobs = hooks.scheduler_events.get("daily") or []
		return "audio_stem.utils.cleanup.cleanup_old_audio_jobs" in daily_jobs
	except Exception:
		return False
