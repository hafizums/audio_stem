# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
from mimetypes import guess_type

import frappe
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.audio import get_audio_duration_seconds
from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.limits import (
	ACTIVE_STATUSES,
	STARTABLE_STATUSES,
	calculate_provider_cost,
	ensure_enabled,
	ensure_single_active_job,
	get_limits_payload,
	get_settings,
	user_has_other_active_job,
	validate_duration,
	validate_file_size,
)

PROVIDER = "WaveSpeed"
PROVIDER_MODEL = "wavespeed-ai/audio-vocal-isolator"
DEFAULT_DISPLAY_CURRENCY = "MYR"
ALLOWED_AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac")
ALLOWED_AUDIO_MIMETYPES = {
	"audio/mpeg",
	"audio/mp3",
	"audio/wav",
	"audio/x-wav",
	"audio/mp4",
	"audio/x-m4a",
	"audio/flac",
	"audio/ogg",
	"application/ogg",
	"audio/aac",
	"audio/x-aac",
}
ALLOWED_VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v", ".mkv")
ALLOWED_VIDEO_MIMETYPES = {
	"video/mp4",
	"video/webm",
	"video/quicktime",
	"video/x-m4v",
	"video/x-matroska",
	"video/mkv",
}


def _get_display_currency() -> str:
	currency = frappe.db.get_single_value("Audio Separation Settings", "display_currency")
	return currency or DEFAULT_DISPLAY_CURRENCY


def _is_system_manager(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _require_login():
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required"), frappe.PermissionError)


def _require_app_access():
	_require_login()
	from audio_stem.utils.pilot_access import ensure_pilot_access

	ensure_pilot_access()


def _get_job_for_user(job_name: str):
	if not frappe.db.exists("Audio Separation Job", job_name):
		frappe.throw(_("Job not found"), frappe.DoesNotExistError)

	owner = frappe.db.get_value("Audio Separation Job", job_name, "user")
	if not _is_system_manager() and owner != frappe.session.user:
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	return frappe.get_doc("Audio Separation Job", job_name)


def _get_attached_file_doc(file_url: str):
	return _get_accessible_file_doc(file_url)


def _get_accessible_file_doc(file_url: str):
	if not file_url:
		frappe.throw(_("Uploaded file not found"))

	file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not file_name:
		frappe.throw(_("Uploaded file not found"))

	file_doc = frappe.get_doc("File", file_name)
	if _is_system_manager():
		return file_doc

	if file_doc.owner == frappe.session.user:
		return file_doc

	if file_doc.attached_to_doctype == "Audio Separation Job":
		job_owner = frappe.db.get_value("Audio Separation Job", file_doc.attached_to_name, "user")
		if job_owner == frappe.session.user:
			return file_doc

	frappe.throw(_("Not permitted"), frappe.PermissionError)
	return file_doc


def _credit_settings_flag_enabled() -> bool:
	return bool(cint(get_settings().credit_management_enabled))


def _check_credit_integration_ready():
	if _credit_settings_flag_enabled():
		from audio_stem.integrations.credit_management_client import credit_management_available

		if not credit_management_available():
			frappe.throw(
				_("Credit Management is enabled but the credit_management app is not installed."),
				frappe.ValidationError,
			)


def _credit_blocked_reason(job, settings=None) -> str | None:
	if not _credit_settings_flag_enabled():
		return None

	from audio_stem.integrations.credit_management_client import (
		credit_management_available,
		get_audio_credit_type,
		get_user_credit_balance,
	)

	if not credit_management_available():
		return _("Credit Management is enabled but the credit_management app is not installed.")

	try:
		credit_type = get_audio_credit_type()
		balance = get_user_credit_balance(job.user, credit_type)
		cost = calculate_provider_cost(job.duration_seconds, settings or get_settings())
		if flt(balance.get("available_balance")) < flt(cost):
			return _("Insufficient available credits for this separation job.")
	except frappe.ValidationError:
		raise
	except Exception:
		return _("Unable to verify credit balance.")

	return None


def _has_vocal_output(job) -> bool:
	return bool(job.vocal_output_url or job.vocal_file)


def _has_instrumental_output(job) -> bool:
	return bool(job.instrumental_output_url or job.instrumental_file)


def _has_zip_output(job) -> bool:
	if not job.get("zip_file"):
		return False
	return bool(_zip_file_exists(job.zip_file))


def _zip_file_exists(file_url: str) -> bool:
	from audio_stem.utils.files import resolve_frappe_file_path

	return bool(resolve_frappe_file_path(file_url))


def _can_download_zip(job) -> bool:
	if job.status != "Completed":
		return False
	if cint(job.get("downstream_assets_stale")):
		return False
	if _has_zip_output(job):
		return True
	return _has_vocal_output(job) and _has_instrumental_output(job)


def _validate_job_for_queue(job, settings=None) -> tuple[bool, str | None]:
	settings = settings or get_settings()

	if not cint(settings.enabled):
		return False, _("Audio separation is disabled in Audio Separation Settings.")

	if job.status in ACTIVE_STATUSES:
		return False, _("This job is already running.")

	if not job.original_file:
		return False, _("Please attach an audio file before starting separation.")

	if not cint(job.duration_seconds):
		return False, _(
			"Audio duration could not be detected. Separation cannot be started until duration is available."
		)

	try:
		file_doc = _get_attached_file_doc(job.original_file)
		validate_file_size(file_doc, settings)
		validate_duration(job.duration_seconds, settings, require_duration=True)
	except frappe.ValidationError as exc:
		return False, str(exc)

	if not _is_system_manager() and user_has_other_active_job(job.user, exclude_job_name=job.name):
		return False, _("You already have an active separation job. Please wait for it to finish.")

	credit_reason = _credit_blocked_reason(job, settings)
	if credit_reason:
		return False, credit_reason

	from audio_stem.utils.abuse_protection import ensure_start_allowed
	from audio_stem.utils.daily_limits import ensure_daily_limits_for_queue

	try:
		if not _is_system_manager(job.user):
			ensure_start_allowed(job.user)
			ensure_daily_limits_for_queue(job.user, job=job)
	except frappe.ValidationError as exc:
		return False, str(exc)

	return True, None


def _can_start_job(job, settings=None) -> tuple[bool, str | None]:
	settings = settings or get_settings()

	if job.status == "Completed":
		return False, _("This job is already completed.")

	if job.status == "Failed":
		return False, _("Use Retry to run this failed job again.")

	if job.status not in STARTABLE_STATUSES:
		return False, _("This job cannot be started.")

	return _validate_job_for_queue(job, settings)


def _can_retry_job(job, settings=None) -> tuple[bool, str | None]:
	if job.status != "Failed":
		return False, _("Only failed jobs can be retried.")

	return _validate_job_for_queue(job, settings or get_settings())


def _can_cancel_job(job, settings=None) -> tuple[bool, str | None]:
	from audio_stem.utils.cancellation import can_cancel_job

	return can_cancel_job(job)


def _job_payload(job):
	return _job_detail_payload(job)


def _job_detail_payload(job):
	from audio_stem.utils.output_reconciliation import sync_karaoke_output_fields_from_files

	if (job.karaoke_status or "Not Started") == "Completed":
		sync_karaoke_output_fields_from_files(job)

	can_start, blocked_reason = _can_start_job(job)
	can_retry, retry_blocked_reason = _can_retry_job(job)
	can_cancel, cancel_blocked_reason = _can_cancel_job(job)
	credit_enabled = _credit_settings_flag_enabled()
	from audio_stem.utils.credit_reconciliation import is_credit_reconciliation_needed

	reconciliation_required = bool(
		credit_enabled and (job.credit_status == "Reconciliation Required" or is_credit_reconciliation_needed(job))
	)
	return {
		"name": job.name,
		"status": job.status,
		"original_file": job.original_file,
		"original_filename": job.original_filename,
		"vocal_output_url": job.vocal_output_url,
		"instrumental_output_url": job.instrumental_output_url,
		"vocal_file": job.vocal_file,
		"instrumental_file": job.instrumental_file,
		"zip_file": job.get("zip_file"),
		"error_message": job.error_message,
		"cleanup_notes": job.get("cleanup_notes"),
		"duration_seconds": cint(job.duration_seconds),
		"provider_cost_usd": flt(job.provider_cost_usd),
		"estimated_cost_usd": calculate_provider_cost(job.duration_seconds),
		"display_currency": _get_display_currency(),
		"creation": job.creation,
		"started_at": job.started_at,
		"completed_at": job.completed_at,
		"can_start": can_start,
		"start_blocked_reason": blocked_reason,
		"can_retry": can_retry,
		"retry_blocked_reason": retry_blocked_reason,
		"can_cancel": can_cancel,
		"cancel_blocked_reason": cancel_blocked_reason,
		"cancellation_requested": cint(job.cancellation_requested),
		"cancelled_at": job.cancelled_at,
		"cancelled_by": job.cancelled_by,
		"cancel_reason": job.cancel_reason,
		"can_zip": _can_download_zip(job),
		"has_vocal": _has_vocal_output(job),
		"has_instrumental": _has_instrumental_output(job),
		"is_active": job.status in ACTIVE_STATUSES,
		"credit_management_enabled": credit_enabled,
		"credit_status": job.credit_status,
		"credit_reservation": job.credit_reservation,
		"reserved_amount": flt(job.reserved_amount),
		"consumed_amount": flt(job.consumed_amount),
		"credit_type": job.credit_type,
		"credit_error": job.credit_error,
		"reconciliation_required": reconciliation_required,
		**_transcription_karaoke_payload(job),
	}


def _karaoke_transcript_source_label(job) -> str:
	from audio_stem.utils.transcript_corrections import resolve_karaoke_transcript_label

	try:
		return resolve_karaoke_transcript_label(job)
	except Exception:
		return "Original Whisper"


def _karaoke_rendered_transcript_source_label(job) -> str | None:
	from audio_stem.utils.transcript_corrections import resolve_karaoke_rendered_transcript_label

	try:
		return resolve_karaoke_rendered_transcript_label(job)
	except Exception:
		return None


def _karaoke_audio_source_label(job) -> str:
	from audio_stem.utils.karaoke_subtitles import karaoke_audio_source_label

	try:
		return karaoke_audio_source_label(job)
	except Exception:
		return "Instrumental track"


def _can_edit_transcript(job) -> bool:
	from audio_stem.utils.transcription_karaoke_controls import KARAOKE_ACTIVE_STATUSES, karaoke_queue_is_stale

	if cint(job.get("downstream_assets_stale")):
		return False
	if (job.transcription_status or "Not Started") != "Completed":
		return False
	if not job.transcript_json_file:
		return False
	karaoke_status = job.karaoke_status or "Not Started"
	if karaoke_status in KARAOKE_ACTIVE_STATUSES and not karaoke_queue_is_stale(job):
		return False
	return True


def _karaoke_background_payload(job) -> dict:
	from audio_stem.utils.karaoke_backgrounds import (
		can_upload_karaoke_background,
		resolve_karaoke_background_video,
	)

	settings = get_settings()
	resolved = resolve_karaoke_background_video(job)
	source = job.get("karaoke_background_source") or resolved.get("source")
	note = job.get("karaoke_background_note") or resolved.get("note")
	file_name = resolved.get("file_name")
	if job.get("karaoke_background_video_file") and not file_name:
		file_name = _resolve_original_filename(job.karaoke_background_video_file)

	return {
		"karaoke_background_source": source,
		"karaoke_background_filename": file_name,
		"karaoke_background_note": note,
		"karaoke_background_duration_seconds": flt(job.get("karaoke_background_duration_seconds"))
		or resolved.get("duration_seconds"),
		"can_upload_karaoke_background": can_upload_karaoke_background(job),
		"allow_user_karaoke_background_upload": bool(cint(settings.allow_user_karaoke_background_upload)),
		"karaoke_background_fit_mode": settings.karaoke_background_fit_mode or "Cover",
	}


def _assert_karaoke_background_mutable(job) -> None:
	from audio_stem.utils.transcription_karaoke_controls import KARAOKE_ACTIVE_STATUSES, karaoke_queue_is_stale

	if (job.karaoke_status or "Not Started") in KARAOKE_ACTIVE_STATUSES and not karaoke_queue_is_stale(job):
		frappe.throw(
			_("Karaoke rendering is active. Wait for it to finish before changing the background video."),
			frappe.ValidationError,
		)


def _get_accessible_background_file(file_url: str):
	if not file_url:
		frappe.throw(_("Background video file was not found."), frappe.ValidationError)

	file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not file_name:
		frappe.throw(_("Background video file was not found."), frappe.ValidationError)

	file_doc = frappe.get_doc("File", file_name)
	if _is_system_manager():
		return file_doc

	if file_doc.owner == frappe.session.user:
		return file_doc

	if file_doc.attached_to_doctype == "Audio Separation Job":
		owner = frappe.db.get_value("Audio Separation Job", file_doc.attached_to_name, "user")
		if owner == frappe.session.user:
			return file_doc

	frappe.throw(_("Not permitted"), frappe.PermissionError)
	return file_doc


def _llm_assistant_payload(job, settings=None) -> dict:
	from audio_stem.integrations.llm_provider import get_llm_assistant_blocked_reason, is_llm_assistant_enabled
	from audio_stem.utils.llm_assistant_controls import can_start_llm_suggestion, llm_queue_is_stale
	from audio_stem.utils.lyric_assistant import get_llm_suggestion_payload

	settings = settings or get_settings()
	can_start, blocked_reason = can_start_llm_suggestion(job, settings=settings)
	payload = get_llm_suggestion_payload(job)
	return {
		"llm_assistant_enabled": is_llm_assistant_enabled(settings),
		"llm_assistant_blocked_reason": get_llm_assistant_blocked_reason(settings),
		"wavespeed_llm_model": settings.wavespeed_llm_model or "deepseek/deepseek-v4-flash",
		"wavespeed_llm_require_manual_approval": bool(cint(settings.wavespeed_llm_require_manual_approval)),
		"can_start_llm_suggestion": can_start,
		"llm_suggestion_blocked_reason": blocked_reason,
		"is_llm_suggestion_active": (
			(job.get("llm_suggestion_status") in ("Queued", "Processing"))
			and not llm_queue_is_stale(job)
		),
		**payload,
	}


def _transcription_karaoke_payload(job):
	from audio_stem.utils.downstream_assets import downstream_assets_payload
	from audio_stem.utils.transcription_karaoke_controls import (
		can_start_karaoke,
		can_start_transcription,
		is_karaoke_enabled,
		karaoke_queue_is_stale,
		transcription_queue_is_stale,
	)
	from audio_stem.integrations.openai_transcription_client import is_openai_transcription_enabled
	from audio_stem.integrations.elevenlabs_scribe_client import is_elevenlabs_scribe_enabled
	from audio_stem.integrations.transcription_provider import (
		PROVIDER_ELEVENLABS,
		PROVIDER_OPENAI,
		get_transcription_provider,
		is_transcription_provider_configured,
	)

	settings = get_settings()
	site_provider = get_transcription_provider(settings)
	can_transcribe, transcription_blocked_reason = can_start_transcription(job, provider=site_provider)
	can_karaoke, karaoke_blocked_reason = can_start_karaoke(job)
	downstream = downstream_assets_payload(job)
	return {
		"openai_enabled": is_openai_transcription_enabled(),
		"elevenlabs_scribe_enabled": is_elevenlabs_scribe_enabled(),
		"transcription_provider": job.get("transcription_provider") or site_provider,
		"transcription_provider_model": job.get("transcription_provider_model"),
		"transcription_language_probability": flt(job.get("transcription_language_probability")),
		"transcription_keyterms_used": job.get("transcription_keyterms_used"),
		"transcription_provider_warning": job.get("transcription_provider_warning"),
		"transcription_enabled": is_transcription_provider_configured(site_provider, settings),
		"site_transcription_provider": site_provider,
		"openai_provider_available": is_transcription_provider_configured(PROVIDER_OPENAI, settings),
		"elevenlabs_provider_available": is_transcription_provider_configured(PROVIDER_ELEVENLABS, settings),
		"karaoke_enabled": is_karaoke_enabled(),
		"transcription_status": job.get("transcription_status") or "Not Started",
		"transcription_source": job.get("transcription_source"),
		"transcription_model": job.get("transcription_model"),
		"transcription_language": job.get("transcription_language"),
		"transcript_text": job.get("transcript_text"),
		"transcript_json_file": job.get("transcript_json_file"),
		"transcript_srt_file": job.get("transcript_srt_file"),
		"transcript_vtt_file": job.get("transcript_vtt_file"),
		"transcription_error": job.get("transcription_error"),
		"transcription_started_at": job.get("transcription_started_at"),
		"transcription_completed_at": job.get("transcription_completed_at"),
		"transcription_cost_usd": flt(job.get("transcription_cost_usd")),
		"transcription_word_count": cint(job.get("transcription_word_count")),
		"transcription_segment_count": cint(job.get("transcription_segment_count")),
		"transcription_detected_language": job.get("transcription_detected_language"),
		"transcription_first_segment_start": flt(job.get("transcription_first_segment_start")),
		"transcription_bad_timestamp_count": cint(job.get("transcription_bad_timestamp_count")),
		"transcription_quality_warning": job.get("transcription_quality_warning"),
		"transcription_quality_unreliable": bool(job.get("transcription_quality_warning")),
		"transcription_suspiciously_short": bool(
			job.get("transcription_quality_warning")
			and cint(job.get("transcription_word_count")) > 0
			and flt(job.duration_seconds) >= 60
			and cint(job.get("transcription_word_count")) < max(12, int(flt(job.duration_seconds) / 60 * 8))
		),
		"karaoke_status": job.get("karaoke_status") or "Not Started",
		"karaoke_style_preset": job.get("karaoke_template") or settings.karaoke_style_preset or "default_1080p",
		"karaoke_ass_file": job.get("karaoke_ass_file"),
		"karaoke_video_file": job.get("karaoke_video_file"),
		"karaoke_background_video_file": job.get("karaoke_background_video_file"),
		"karaoke_subtitle_json_file": job.get("karaoke_subtitle_json_file"),
		"karaoke_engine_version": job.get("karaoke_engine_version"),
		"karaoke_error": job.get("karaoke_error"),
		"karaoke_started_at": job.get("karaoke_started_at"),
		"karaoke_completed_at": job.get("karaoke_completed_at"),
		"karaoke_video_render_enabled": bool(cint(settings.karaoke_video_render_enabled)),
		"karaoke_ass_enabled": bool(cint(settings.karaoke_ass_enabled)),
		"can_start_transcription": can_transcribe,
		"transcription_blocked_reason": transcription_blocked_reason,
		"can_start_karaoke": can_karaoke,
		"karaoke_blocked_reason": karaoke_blocked_reason,
		"has_transcript_json": bool(job.get("transcript_json_file")),
		"has_transcript_srt": bool(job.get("transcript_srt_file")),
		"has_transcript_vtt": bool(job.get("transcript_vtt_file")),
		"has_karaoke_ass": bool(job.get("karaoke_ass_file")),
		"has_karaoke_video": bool(job.get("karaoke_video_file")),
		"has_karaoke_background_video": bool(job.get("karaoke_background_video_file")),
		"is_transcription_active": (
			job.get("transcription_status") in ("Queued", "Processing")
			and not transcription_queue_is_stale(job)
		),
		"is_karaoke_active": (
			job.get("karaoke_status") in ("Queued", "Rendering") and not karaoke_queue_is_stale(job)
		),
		"karaoke_style_preset_default": settings.karaoke_style_preset or "default_1080p",
		"has_default_karaoke_background_video": bool(settings.get("default_karaoke_background_video")),
		"default_transcription_language": settings.default_transcription_language,
		"transcription_use_vocal_stem_by_default": bool(cint(settings.transcription_use_vocal_stem_by_default)),
		"transcription_prompt_enabled": bool(cint(settings.transcription_prompt_enabled)),
		"transcription_prompt_text": settings.transcription_prompt_text,
		"transcription_audio_preprocess_enabled": bool(cint(settings.transcription_audio_preprocess_enabled)),
		"transcription_chunking_enabled": bool(cint(settings.transcription_chunking_enabled)),
		"elevenlabs_scribe_model": settings.elevenlabs_scribe_model or "scribe_v2",
		"elevenlabs_language_code": settings.elevenlabs_language_code,
		"elevenlabs_use_keyterms": bool(cint(settings.elevenlabs_use_keyterms)),
		"elevenlabs_no_verbatim": bool(cint(settings.elevenlabs_no_verbatim)),
		"elevenlabs_tag_audio_events": bool(cint(settings.elevenlabs_tag_audio_events)),
		"elevenlabs_diarize": bool(cint(settings.elevenlabs_diarize)),
		"manual_transcript_status": job.get("manual_transcript_status") or "Not Started",
		"manual_transcript_text": job.get("manual_transcript_text"),
		"manual_transcript_json_file": job.get("manual_transcript_json_file"),
		"manual_transcript_srt_file": job.get("manual_transcript_srt_file"),
		"manual_transcript_vtt_file": job.get("manual_transcript_vtt_file"),
		"manual_transcript_updated_at": job.get("manual_transcript_updated_at"),
		"manual_transcript_approved_at": job.get("manual_transcript_approved_at"),
		"has_manual_transcript": bool(job.get("manual_transcript_json_file")),
		"has_manual_transcript_srt": bool(job.get("manual_transcript_srt_file")),
		"has_manual_transcript_vtt": bool(job.get("manual_transcript_vtt_file")),
		"manual_transcript_is_approved": (job.get("manual_transcript_status") or "Not Started") == "Approved",
		"karaoke_use_manual_transcript": bool(cint(job.get("karaoke_use_manual_transcript"))),
		"karaoke_source_mode": job.get("karaoke_source_mode") or "Auto",
		"karaoke_audio_mode": job.get("karaoke_audio_mode") or "Auto",
		"karaoke_audio_source_label": _karaoke_audio_source_label(job),
		"karaoke_style_override_enabled": bool(cint(job.get("karaoke_style_override_enabled"))),
		"karaoke_style_source": job.get("karaoke_style_source"),
		"karaoke_transcript_source_label": _karaoke_transcript_source_label(job),
		"karaoke_rendered_transcript_source_label": _karaoke_rendered_transcript_source_label(job),
		"can_edit_transcript": _can_edit_transcript(job),
		**_karaoke_background_payload(job),
		**downstream,
		**_llm_assistant_payload(job, settings=settings),
	}


def _resolve_original_filename(file_url: str) -> str | None:
	file_name = frappe.db.get_value("File", {"file_url": file_url}, "file_name")
	if file_name:
		return file_name
	return os.path.basename(file_url) if file_url else None


def _validate_audio_upload(filename: str, content_type: str | None = None):
	ext = os.path.splitext(filename or "")[1].lower()
	mime = (content_type or guess_type(filename)[0] or "").lower()

	if ext in ALLOWED_AUDIO_EXTENSIONS:
		return
	if mime in ALLOWED_AUDIO_MIMETYPES:
		return

	frappe.throw(_("Please upload a supported audio file (MP3, WAV, M4A, FLAC, OGG, AAC)."))


def _validate_video_upload(filename: str, content_type: str | None = None):
	ext = os.path.splitext(filename or "")[1].lower()
	mime = (content_type or guess_type(filename)[0] or "").lower()

	if ext in ALLOWED_VIDEO_EXTENSIONS:
		return
	if mime in ALLOWED_VIDEO_MIMETYPES:
		return

	frappe.throw(_("Please upload a supported video file (MP4, WEBM, MOV, MKV)."))


def _save_uploaded_video(upload, *, attached_to_doctype: str | None = None, attached_to_name: str | None = None, attached_to_field: str | None = None) -> dict:
	settings = get_settings()
	ensure_enabled(settings)

	filename = upload.filename
	if not filename:
		frappe.throw(_("No file uploaded"))

	content = upload.stream.read()
	_validate_video_upload(filename, upload.content_type)

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"is_private": 1,
			"content": content,
			"attached_to_doctype": attached_to_doctype,
			"attached_to_name": attached_to_name,
			"attached_to_field": attached_to_field,
		}
	)
	file_doc.save(ignore_permissions=True)
	validate_file_size(file_doc, settings)

	return {
		"file_url": file_doc.file_url,
		"file_name": file_doc.file_name,
	}


def _save_uploaded_audio(upload) -> dict:
	settings = get_settings()
	ensure_enabled(settings)

	filename = upload.filename
	if not filename:
		frappe.throw(_("No file uploaded"))

	content = upload.stream.read()
	_validate_audio_upload(filename, upload.content_type)

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"is_private": 1,
			"content": content,
		}
	)
	file_doc.save(ignore_permissions=True)
	validate_file_size(file_doc, settings)

	return {
		"file_url": file_doc.file_url,
		"file_name": file_doc.file_name,
	}


@frappe.whitelist()
def upload_audio_file():
	_require_app_access()

	files = frappe.request.files
	if "file" not in files:
		frappe.throw(_("No file uploaded"))

	return _save_uploaded_audio(files["file"])


@frappe.whitelist()
def create_job_from_file(file_url: str):
	_require_app_access()

	from audio_stem.utils.abuse_protection import ensure_create_allowed
	from audio_stem.utils.audit_log import log_audit

	ensure_create_allowed()

	if not file_url:
		frappe.throw(_("file_url is required"))

	settings = get_settings()
	ensure_enabled(settings)

	file_doc = _get_attached_file_doc(file_url)
	validate_file_size(file_doc, settings)

	duration_seconds = None
	try:
		duration_seconds = get_audio_duration_seconds(file_doc.get_full_path())
	except Exception:
		duration_seconds = None

	if duration_seconds:
		validate_duration(duration_seconds, settings)

	from audio_stem.integrations.credit_management_client import is_credit_management_enabled

	job = frappe.get_doc(
		{
			"doctype": "Audio Separation Job",
			"user": frappe.session.user,
			"status": "Draft",
			"original_file": file_url,
			"original_filename": _resolve_original_filename(file_url),
			"duration_seconds": duration_seconds,
			"credit_status": "Pending" if is_credit_management_enabled() else "Not Required",
		}
	)
	job.insert(ignore_permissions=True)

	log_audit(
		"Create Job",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message=f"Job created from uploaded file.",
		metadata={"original_filename": job.original_filename},
	)

	return _job_payload(job)


@frappe.whitelist()
def get_job_status(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	return _job_detail_payload(job)


@frappe.whitelist()
def get_job_detail(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	return _job_detail_payload(job)


@frappe.whitelist()
def get_page_settings():
	_require_login()
	from audio_stem.integrations.credit_management_client import get_audio_credit_type, is_credit_management_enabled
	from audio_stem.integrations.elevenlabs_scribe_client import is_elevenlabs_scribe_enabled
	from audio_stem.integrations.llm_provider import is_llm_assistant_enabled
	from audio_stem.integrations.transcription_provider import (
		PROVIDER_ELEVENLABS,
		PROVIDER_OPENAI,
		get_transcription_provider,
		is_transcription_provider_configured,
	)
	from audio_stem.utils.karaoke_style_settings import karaoke_style_settings_payload
	from audio_stem.utils.daily_limits import get_daily_limit_status
	from audio_stem.utils.pilot_access import get_pilot_access_status

	limits = get_limits_payload()
	pilot = get_pilot_access_status()
	daily_usage = get_daily_limit_status() if pilot.get("pilot_access_allowed") else None
	settings = get_settings()
	site_provider = get_transcription_provider(settings)
	return {
		**limits,
		**pilot,
		"daily_usage": daily_usage,
		"display_currency": _get_display_currency(),
		"credit_management_enabled": is_credit_management_enabled(),
		"credit_type": get_audio_credit_type() if is_credit_management_enabled() else None,
		"accepted_file_types": "MP3, WAV, M4A, FLAC, OGG, AAC",
		"is_system_manager": _is_system_manager(),
		"openai_enabled": bool(cint(settings.openai_enabled)),
		"elevenlabs_scribe_enabled": is_elevenlabs_scribe_enabled(),
		"transcription_provider": site_provider,
		"transcription_enabled": is_transcription_provider_configured(site_provider, settings),
		"openai_provider_available": is_transcription_provider_configured(PROVIDER_OPENAI, settings),
		"elevenlabs_provider_available": is_transcription_provider_configured(PROVIDER_ELEVENLABS, settings),
		"karaoke_enabled": bool(cint(settings.karaoke_enabled)),
		"karaoke_ass_enabled": bool(cint(get_settings().karaoke_ass_enabled)),
		"karaoke_video_render_enabled": bool(cint(get_settings().karaoke_video_render_enabled)),
		"karaoke_style_preset": settings.karaoke_style_preset or "default_1080p",
		"karaoke_include_instrumental_audio": bool(cint(settings.karaoke_include_instrumental_audio)),
		"has_default_karaoke_background_video": bool(settings.default_karaoke_background_video),
		"subtitle_max_words_per_line": cint(get_settings().subtitle_max_words_per_line) or 5,
		"subtitle_max_line_duration_seconds": flt(get_settings().subtitle_max_line_duration_seconds) or 4.0,
		"subtitle_min_word_duration_seconds": flt(get_settings().subtitle_min_word_duration_seconds) or 0.08,
		"subtitle_snap_overlaps": bool(cint(get_settings().subtitle_snap_overlaps)),
		"default_transcription_language": settings.default_transcription_language,
		"transcription_max_file_size_mb": cint(settings.transcription_max_file_size_mb) or 25,
		"transcription_use_vocal_stem_by_default": bool(cint(settings.transcription_use_vocal_stem_by_default)),
		"transcription_prompt_enabled": bool(cint(settings.transcription_prompt_enabled)),
		"transcription_prompt_text": settings.transcription_prompt_text,
		"transcription_audio_preprocess_enabled": bool(cint(settings.transcription_audio_preprocess_enabled)),
		"transcription_chunking_enabled": bool(cint(settings.transcription_chunking_enabled)),
		"elevenlabs_scribe_model": settings.elevenlabs_scribe_model or "scribe_v2",
		"elevenlabs_language_code": settings.elevenlabs_language_code,
		"elevenlabs_use_keyterms": bool(cint(settings.elevenlabs_use_keyterms)),
		"elevenlabs_no_verbatim": bool(cint(settings.elevenlabs_no_verbatim)),
		"elevenlabs_tag_audio_events": bool(cint(settings.elevenlabs_tag_audio_events)),
		"elevenlabs_diarize": bool(cint(settings.elevenlabs_diarize)),
		"llm_assistant_enabled": is_llm_assistant_enabled(),
		"wavespeed_llm_model": settings.wavespeed_llm_model or "deepseek/deepseek-v4-flash",
		"wavespeed_llm_require_manual_approval": bool(cint(settings.wavespeed_llm_require_manual_approval)),
		**karaoke_style_settings_payload(),
	}


@frappe.whitelist()
def update_karaoke_style_settings(**kwargs):
	"""Update karaoke subtitle style settings (System Manager only)."""
	_require_login()
	if not _is_system_manager():
		frappe.throw(_("Only System Managers can update karaoke style settings."), frappe.PermissionError)

	from audio_stem.utils.karaoke_style_settings import karaoke_style_settings_payload

	settings = get_settings()
	allowed_fields = set(karaoke_style_settings_payload().keys())
	for fieldname, value in kwargs.items():
		if fieldname not in allowed_fields:
			continue
		if value is None or value == "":
			continue
		settings.set(fieldname, value)
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	return karaoke_style_settings_payload(settings)


@frappe.whitelist()
def get_karaoke_style_for_job(job_name: str):
	"""Return global, override, and effective karaoke style for a job."""
	_require_login()
	job = _get_job_for_user(job_name)
	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.karaoke_style_settings import karaoke_style_for_job_payload

	payload = karaoke_style_for_job_payload(job)
	payload["can_edit_site_style"] = _is_system_manager()
	payload["can_edit_job_style"] = True
	log_audit(
		"View Karaoke Style",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message="Karaoke style viewed.",
	)
	return payload


@frappe.whitelist()
def update_karaoke_style_for_job(job_name: str, **kwargs):
	"""Update per-job karaoke style overrides for the current user's job."""
	_require_login()
	job = _get_job_for_user(job_name)
	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.karaoke_style_settings import (
		JOB_STYLE_UPDATE_FIELDS,
		apply_job_style_update,
		karaoke_style_for_job_payload,
	)

	style_payload = {key: kwargs[key] for key in JOB_STYLE_UPDATE_FIELDS if key in kwargs}
	if not style_payload:
		frappe.throw(_("No karaoke style fields were provided."), frappe.ValidationError)

	apply_job_style_update(job, style_payload)
	job.save(ignore_permissions=True)
	frappe.db.commit()
	log_audit(
		"Update Karaoke Style",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message="Per-job karaoke style updated.",
	)
	return karaoke_style_for_job_payload(job)


@frappe.whitelist()
def reset_karaoke_style_for_job(job_name: str):
	"""Clear per-job karaoke style overrides and revert to global settings."""
	_require_login()
	job = _get_job_for_user(job_name)
	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.karaoke_style_settings import (
		clear_job_style_overrides,
		karaoke_style_for_job_payload,
	)

	clear_job_style_overrides(job)
	job.save(ignore_permissions=True)
	frappe.db.commit()
	log_audit(
		"Reset Karaoke Style",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message="Per-job karaoke style reset to site default.",
	)
	return karaoke_style_for_job_payload(job)


@frappe.whitelist()
def get_my_credit_balance():
	_require_app_access()
	from audio_stem.integrations.credit_management_client import (
		credit_management_available,
		get_audio_credit_type,
		get_user_credit_balance,
		is_credit_management_enabled,
	)

	if not is_credit_management_enabled():
		return {"enabled": False}

	if not credit_management_available():
		return {
			"enabled": True,
			"error": _("Credit Management is enabled but the credit_management app is not installed."),
		}

	try:
		balance = get_user_credit_balance(frappe.session.user)
		return {
			"enabled": True,
			"credit_type": get_audio_credit_type(),
			"current_balance": flt(balance.get("current_balance")),
			"reserved_balance": flt(balance.get("reserved_balance")),
			"available_balance": flt(balance.get("available_balance")),
		}
	except Exception as exc:
		return {
			"enabled": True,
			"credit_type": get_audio_credit_type(),
			"error": safe_error_message(exc),
		}


@frappe.whitelist()
def get_recent_jobs(limit=10):
	_require_app_access()
	limit = min(cint(limit) or 10, 50)
	credit_enabled = _credit_settings_flag_enabled()

	filters = {"user": frappe.session.user}
	fields = [
		"name",
		"user",
		"original_file",
		"status",
		"creation",
		"completed_at",
		"duration_seconds",
		"provider_cost_usd",
		"original_filename",
		"vocal_output_url",
		"instrumental_output_url",
		"vocal_file",
		"instrumental_file",
		"zip_file",
		"error_message",
	]
	if credit_enabled:
		fields.append("credit_status")

	jobs = frappe.get_all(
		"Audio Separation Job",
		filters=filters,
		fields=fields,
		order_by="creation desc",
		limit=limit,
		ignore_permissions=True,
	)

	rows = []
	for row in jobs:
		job = frappe._dict(row)
		can_retry, _ = _can_retry_job(job)
		can_cancel, _ = _can_cancel_job(job)
		rows.append(
			{
				"name": job.name,
				"original_filename": job.original_filename,
				"status": job.status,
				"credit_status": job.credit_status if credit_enabled else None,
				"duration_seconds": cint(job.duration_seconds),
				"provider_cost_usd": flt(job.provider_cost_usd),
				"creation": job.creation,
				"completed_at": job.completed_at,
				"has_vocal": _has_vocal_output(job),
				"has_instrumental": _has_instrumental_output(job),
				"error_summary": job.error_message if job.status == "Failed" else None,
				"can_retry": can_retry,
				"can_cancel": can_cancel,
				"can_zip": _can_download_zip(job),
			}
		)
	return rows


def _prepare_and_queue_job(
	job,
	settings,
	*,
	preserve_outputs: bool = False,
	enqueue_failure_status: str = "Draft",
):
	ensure_enabled(settings)
	_check_credit_integration_ready()

	if not job.original_file:
		frappe.throw(_("Please attach an audio file before starting separation."))

	file_doc = _get_attached_file_doc(job.original_file)
	validate_file_size(file_doc, settings)
	validate_duration(job.duration_seconds, settings, require_duration=True)

	if not _is_system_manager():
		ensure_single_active_job(job.user, exclude_job_name=job.name)

	job.provider_cost_usd = calculate_provider_cost(job.duration_seconds, settings)

	from audio_stem.integrations.credit_management_client import (
		is_credit_management_enabled,
		release_job_reservation,
		reserve_job_credits,
	)

	if is_credit_management_enabled():
		if job.credit_status in ("Released", "Failed", "Reconciliation Required"):
			job.credit_reservation = None
			job.reserved_amount = 0
			job.consumed_amount = 0
			job.credit_error = None

		reserve_job_credits(job)
	else:
		job.credit_status = "Not Required"
		job.credit_reservation = None
		job.reserved_amount = 0
		job.consumed_amount = 0
		job.credit_type = None
		job.credit_error = None

	job.status = "Queued"
	job.provider = PROVIDER
	job.provider_model = PROVIDER_MODEL
	job.error_message = None
	job.credit_error = None
	if not preserve_outputs:
		job.vocal_output_url = None
		job.instrumental_output_url = None
		job.vocal_file = None
		job.instrumental_file = None
	job.started_at = None
	job.completed_at = None
	job.save(ignore_permissions=True)

	try:
		frappe.enqueue(
			"audio_stem.workers.separation_worker.process_audio_separation",
			queue="long",
			job_id=f"audio_separation:{job.name}",
			name=job.name,
		)
	except Exception as exc:
		if is_credit_management_enabled() and job.credit_reservation and job.credit_status == "Reserved":
			try:
				release_job_reservation(job, reason="Failed to enqueue separation job")
			except Exception as release_exc:
				job.credit_error = safe_error_message(release_exc)
				frappe.log_error(
					title=f"Credit release failed after enqueue error for {job.name}",
					message=frappe.get_traceback(),
				)
		job.status = enqueue_failure_status
		job.error_message = safe_error_message(exc)
		job.save(ignore_permissions=True)
		raise exc


@frappe.whitelist()
def start_separation(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	settings = get_settings()

	from audio_stem.utils.audit_log import log_audit

	if job.status in ACTIVE_STATUSES:
		return {**_job_payload(job), "already_active": True}

	if job.status == "Completed":
		frappe.throw(_("This job is already completed."))

	if job.status != "Draft":
		if job.status == "Failed":
			frappe.throw(_("Use retry to run this failed job again."))
		frappe.throw(_("Job can only be started from Draft status."))

	can_start, blocked_reason = _can_start_job(job, settings)
	if not can_start:
		frappe.throw(blocked_reason or _("This job cannot be started."))

	_prepare_and_queue_job(job, settings, preserve_outputs=False, enqueue_failure_status="Draft")
	log_audit("Start Job", reference_doctype=job.doctype, reference_name=job.name, message="Job queued.")
	return {**_job_payload(job), "already_active": False}


@frappe.whitelist()
def retry_failed_job(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	settings = get_settings()

	from audio_stem.utils.audit_log import log_audit

	if job.status in ACTIVE_STATUSES:
		return {**_job_payload(job), "already_active": True}

	if job.status != "Failed":
		frappe.throw(_("Only failed jobs can be retried."))

	can_retry, blocked_reason = _can_retry_job(job, settings)
	if not can_retry:
		frappe.throw(blocked_reason or _("This job cannot be retried."))

	_prepare_and_queue_job(
		job,
		settings,
		preserve_outputs=True,
		enqueue_failure_status="Failed",
	)
	log_audit("Retry Job", reference_doctype=job.doctype, reference_name=job.name, message="Failed job retried.")
	return {**_job_payload(job), "already_active": False}


@frappe.whitelist()
def cancel_job(job_name: str, cancel_reason: str | None = None):
	_require_app_access()
	job = _get_job_for_user(job_name)

	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.cancellation import apply_cancel_request

	result = apply_cancel_request(job, cancelled_by=frappe.session.user, cancel_reason=cancel_reason)
	log_audit(
		"Cancel Job",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message=result.get("message"),
	)
	job.reload()
	return {**_job_detail_payload(job), **result}


@frappe.whitelist()
def create_job_zip(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)

	from audio_stem.utils.audit_log import log_audit

	if job.status != "Completed":
		frappe.throw(_("ZIP download is only available for completed jobs."), frappe.ValidationError)

	from audio_stem.utils.zip_download import create_job_zip_file

	zip_url = create_job_zip_file(job)
	job.zip_file = zip_url
	job.save(ignore_permissions=True)
	log_audit("Create ZIP", reference_doctype=job.doctype, reference_name=job.name, message="ZIP created.")
	return {"zip_file": zip_url, "job_name": job.name}


@frappe.whitelist()
def start_transcription(
	job_name: str,
	source: str | None = None,
	language: str | None = None,
	prompt: str | None = None,
	provider: str | None = None,
	scribe_model: str | None = None,
	keyterms: str | None = None,
	no_verbatim: int | None = None,
	tag_audio_events: int | None = None,
	diarize: int | None = None,
):
	_require_app_access()
	job = _get_job_for_user(job_name)
	settings = get_settings()

	from audio_stem.integrations.transcription_provider import (
		get_transcription_provider_blocked_reason,
		resolve_transcription_provider,
	)
	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.scribe_keyterms import parse_keyterms, validate_keyterms
	from audio_stem.utils.transcription_karaoke_controls import (
		TRANSCRIPTION_ACTIVE_STATUSES,
		can_start_transcription_source,
		enqueue_transcription,
		transcription_queue_is_stale,
	)
	from audio_stem.utils.transcription_quality import (
		resolve_default_transcription_source,
		resolve_transcription_language,
		validate_transcription_prompt_text,
	)

	selected_provider = resolve_transcription_provider(provider, settings)
	blocked_reason = get_transcription_provider_blocked_reason(selected_provider, settings)
	if blocked_reason:
		frappe.throw(blocked_reason, frappe.ValidationError)

	if (job.transcription_status or "Not Started") in TRANSCRIPTION_ACTIVE_STATUSES:
		if not transcription_queue_is_stale(job):
			return {**_job_detail_payload(job), "already_active": True}

	source = (source or resolve_default_transcription_source(settings)).strip()
	can_start, blocked_reason = can_start_transcription_source(job, source, provider=selected_provider)
	if not can_start:
		frappe.throw(blocked_reason or _("Transcription cannot be started."), frappe.ValidationError)

	from audio_stem.utils.abuse_protection import ensure_start_allowed

	if not _is_system_manager(job.user):
		ensure_start_allowed(job.user)

	language = resolve_transcription_language(language or settings.default_transcription_language, settings)
	if prompt:
		validate_transcription_prompt_text(prompt)
	if keyterms:
		validate_keyterms(parse_keyterms(keyterms))
	enqueue_transcription(
		job,
		source=source,
		language=language,
		prompt=prompt,
		provider=selected_provider,
		scribe_model=scribe_model,
		keyterms=keyterms,
		no_verbatim=no_verbatim,
		tag_audio_events=tag_audio_events,
		diarize=diarize,
	)
	log_audit(
		"Start Transcription",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message=f"Transcription queued from {source} via {selected_provider}.",
		metadata={"source": source, "language": language, "provider": selected_provider},
	)
	job.reload()
	return {**_job_detail_payload(job), "already_active": False}


@frappe.whitelist()
def get_transcription_status(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	return _transcription_karaoke_payload(job)


@frappe.whitelist()
def get_transcription_input_check(job_name: str, source: str = "Vocal", probe_external: int = 0):
	"""Read-only preview of which audio file/profile would be sent to whisper-1."""
	_require_app_access()
	job = _get_job_for_user(job_name)
	from audio_stem.utils.transcription_input_check import build_whisper_input_report

	return build_whisper_input_report(job, source, probe_external=cint(probe_external))


@frappe.whitelist()
def download_transcript_asset(job_name: str, asset_type: str):
	_require_app_access()
	job = _get_job_for_user(job_name)

	from audio_stem.utils.audit_log import log_audit

	asset_type = (asset_type or "").strip().lower()
	field_map = {
		"json": "transcript_json_file",
		"srt": "transcript_srt_file",
		"vtt": "transcript_vtt_file",
	}
	fieldname = field_map.get(asset_type)
	if not fieldname or not job.get(fieldname):
		frappe.throw(_("Transcript file is not available."), frappe.DoesNotExistError)

	log_audit(
		"Download Transcript",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message=f"Downloaded {asset_type.upper()} transcript.",
	)
	return {"file_url": job.get(fieldname), "asset_type": asset_type}


@frappe.whitelist()
def start_karaoke_render(
	job_name: str,
	template: str | None = None,
	karaoke_source_mode: str | None = None,
	karaoke_audio_mode: str | None = None,
):
	_require_app_access()
	job = _get_job_for_user(job_name)

	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.transcription_karaoke_controls import (
		KARAOKE_ACTIVE_STATUSES,
		can_start_karaoke,
		enqueue_karaoke,
		is_karaoke_enabled,
		karaoke_queue_is_stale,
	)
	from audio_stem.utils.karaoke_subtitles import KARAOKE_AUDIO_MODES, resolve_karaoke_style_preset
	from audio_stem.utils.transcript_corrections import KARAOKE_SOURCE_MODES

	if not is_karaoke_enabled():
		frappe.throw(_("Karaoke rendering is disabled."), frappe.ValidationError)

	if (job.karaoke_status or "Not Started") in KARAOKE_ACTIVE_STATUSES:
		if not karaoke_queue_is_stale(job):
			return {**_job_detail_payload(job), "already_active": True}

	can_start, blocked_reason = can_start_karaoke(job)
	if not can_start:
		frappe.throw(blocked_reason or _("Karaoke rendering cannot be started."), frappe.ValidationError)

	if karaoke_source_mode:
		mode = karaoke_source_mode.strip()
		if mode not in KARAOKE_SOURCE_MODES:
			frappe.throw(_("Invalid karaoke source mode."), frappe.ValidationError)
		job.karaoke_source_mode = mode
		job.save(ignore_permissions=True)

	if karaoke_audio_mode:
		audio_mode = karaoke_audio_mode.strip()
		if audio_mode not in KARAOKE_AUDIO_MODES:
			frappe.throw(_("Invalid karaoke audio mode."), frappe.ValidationError)
		job.karaoke_audio_mode = audio_mode
		job.save(ignore_permissions=True)

	if job.karaoke_source_mode == "Manual Corrected" and not job.manual_transcript_json_file:
		frappe.throw(_("Manual corrected transcript is required for this karaoke source mode."), frappe.ValidationError)

	style_preset = resolve_karaoke_style_preset(template)
	enqueue_karaoke(job, template=style_preset)
	log_audit(
		"Start Karaoke",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message=f"Karaoke subtitle generation queued with style {style_preset}.",
		metadata={
			"style_preset": style_preset,
			"karaoke_source_mode": job.karaoke_source_mode or "Auto",
			"karaoke_audio_mode": job.karaoke_audio_mode or "Auto",
		},
	)
	job.reload()
	return {**_job_detail_payload(job), "already_active": False}


@frappe.whitelist()
def get_karaoke_status(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	return _transcription_karaoke_payload(job)


@frappe.whitelist()
def set_karaoke_background_video(job_name: str, file_url: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	_assert_karaoke_background_mutable(job)

	from audio_stem.utils.karaoke_backgrounds import can_upload_karaoke_background, validate_background_video_file

	if not can_upload_karaoke_background(job):
		frappe.throw(_("User background video uploads are disabled."), frappe.PermissionError)

	file_url = (file_url or "").strip()
	if not file_url:
		frappe.throw(_("Background video file was not found."), frappe.ValidationError)

	file_doc = _get_accessible_background_file(file_url)
	validate_background_video_file(file_doc)

	job.karaoke_background_video_file = file_doc.file_url
	job.karaoke_background_source = None
	job.karaoke_background_note = None
	job.karaoke_background_duration_seconds = None
	job.save(ignore_permissions=True)
	return _transcription_karaoke_payload(job)


@frappe.whitelist()
def clear_karaoke_background_video(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	_assert_karaoke_background_mutable(job)

	job.karaoke_background_video_file = None
	job.karaoke_background_source = None
	job.karaoke_background_note = None
	job.karaoke_background_duration_seconds = None
	job.save(ignore_permissions=True)
	return _transcription_karaoke_payload(job)


@frappe.whitelist()
def upload_karaoke_background_video(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	_assert_karaoke_background_mutable(job)

	from audio_stem.utils.karaoke_backgrounds import can_upload_karaoke_background, validate_background_video_file

	if not can_upload_karaoke_background(job):
		frappe.throw(_("User background video uploads are disabled."), frappe.PermissionError)

	files = frappe.request.files
	if not files or "file" not in files:
		frappe.throw(_("No file uploaded"))

	uploaded = _save_uploaded_video(
		files["file"],
		attached_to_doctype=job.doctype,
		attached_to_name=job.name,
		attached_to_field="karaoke_background_video_file",
	)
	file_doc = frappe.get_doc("File", {"file_url": uploaded["file_url"]})
	validate_background_video_file(file_doc)
	job.karaoke_background_video_file = uploaded["file_url"]
	job.karaoke_background_source = None
	job.karaoke_background_note = None
	job.karaoke_background_duration_seconds = None
	job.save(ignore_permissions=True)
	return _transcription_karaoke_payload(job)


def _parse_transcript_payload(payload):
	if isinstance(payload, str):
		payload = frappe.parse_json(payload)
	if not isinstance(payload, dict):
		frappe.throw(_("Transcript payload must be an object."), frappe.ValidationError)
	return payload


def _manual_transcript_payload(job):
	return {
		"manual_transcript_status": job.manual_transcript_status or "Not Started",
		"manual_transcript_text": job.manual_transcript_text,
		"manual_transcript_json_file": job.manual_transcript_json_file,
		"manual_transcript_srt_file": job.manual_transcript_srt_file,
		"manual_transcript_vtt_file": job.manual_transcript_vtt_file,
		"manual_transcript_updated_at": job.manual_transcript_updated_at,
		"manual_transcript_approved_at": job.manual_transcript_approved_at,
		"has_manual_transcript": bool(job.manual_transcript_json_file),
		"has_manual_transcript_srt": bool(job.manual_transcript_srt_file),
		"has_manual_transcript_vtt": bool(job.manual_transcript_vtt_file),
		"manual_transcript_is_approved": (job.manual_transcript_status or "Not Started") == "Approved",
	}


@frappe.whitelist()
def get_transcript_for_edit(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	if not _can_edit_transcript(job):
		frappe.throw(_("Transcript editing is not available for this job."), frappe.ValidationError)

	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.transcript_corrections import load_transcript_for_edit

	result = load_transcript_for_edit(job)
	log_audit(
		"View Transcript Editor",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message="Opened transcript editor.",
	)
	return {**result, **_manual_transcript_payload(job)}


@frappe.whitelist()
def save_transcript_corrections(job_name: str, payload):
	_require_app_access()
	job = _get_job_for_user(job_name)
	if not _can_edit_transcript(job):
		frappe.throw(_("Transcript editing is not available for this job."), frappe.ValidationError)

	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.transcript_corrections import save_manual_transcript

	parsed = _parse_transcript_payload(payload)
	result = save_manual_transcript(job, parsed, status="Saved")
	log_audit(
		"Save Transcript Correction",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message="Saved manual transcript corrections.",
	)
	job.reload()
	return {**result, **_manual_transcript_payload(job)}


@frappe.whitelist()
def approve_transcript_corrections(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	if not _can_edit_transcript(job):
		frappe.throw(_("Transcript editing is not available for this job."), frappe.ValidationError)

	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.transcript_corrections import approve_manual_transcript

	result = approve_manual_transcript(job)
	log_audit(
		"Approve Transcript Correction",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message="Approved manual transcript corrections.",
	)
	job.reload()
	return {**result, **_manual_transcript_payload(job)}


@frappe.whitelist()
def reset_manual_transcript(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	if not _can_edit_transcript(job):
		frappe.throw(_("Transcript editing is not available for this job."), frappe.ValidationError)

	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.transcript_corrections import reset_manual_transcript as reset_manual

	result = reset_manual(job)
	log_audit(
		"Reset Transcript Correction",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message="Reset manual transcript corrections.",
	)
	job.reload()
	return {**result, **_manual_transcript_payload(job), **_transcription_karaoke_payload(job)}


@frappe.whitelist()
def regenerate_subtitle_assets(job_name: str, source: str = "manual"):
	_require_app_access()
	job = _get_job_for_user(job_name)
	if (job.transcription_status or "Not Started") != "Completed":
		frappe.throw(_("Completed transcription is required."), frappe.ValidationError)

	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.transcript_corrections import (
		generate_srt_from_manual_transcript,
		generate_vtt_from_manual_transcript,
		load_transcript_json_from_file_url,
	)
	from audio_stem.utils.transcription_assets import (
		write_srt_from_segments_or_words,
		write_vtt_from_segments_or_words,
	)

	source = (source or "manual").strip().lower()
	if source == "manual":
		if not job.manual_transcript_json_file:
			frappe.throw(_("Manual transcript is not available."), frappe.ValidationError)
		generate_srt_from_manual_transcript(job)
		generate_vtt_from_manual_transcript(job)
	elif source == "whisper":
		if not job.transcript_json_file:
			frappe.throw(_("Original Whisper transcript is not available."), frappe.ValidationError)
		data = load_transcript_json_from_file_url(job.transcript_json_file)
		write_srt_from_segments_or_words(job, data)
		write_vtt_from_segments_or_words(job, data)
	else:
		frappe.throw(_("Invalid subtitle source."), frappe.ValidationError)

	job.save(ignore_permissions=True)
	log_audit(
		"Regenerate Subtitle Assets",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message=f"Regenerated subtitle assets from {source}.",
		metadata={"source": source},
	)
	job.reload()
	return {**_manual_transcript_payload(job), **_transcription_karaoke_payload(job)}


@frappe.whitelist()
def download_manual_transcript_asset(job_name: str, asset_type: str):
	_require_app_access()
	job = _get_job_for_user(job_name)

	asset_type = (asset_type or "").strip().lower()
	field_map = {
		"json": "manual_transcript_json_file",
		"srt": "manual_transcript_srt_file",
		"vtt": "manual_transcript_vtt_file",
	}
	fieldname = field_map.get(asset_type)
	if not fieldname or not job.get(fieldname):
		frappe.throw(_("Manual transcript file is not available."), frappe.DoesNotExistError)
	return {"file_url": job.get(fieldname), "asset_type": asset_type}


def _require_llm_assistant(job):
	from audio_stem.integrations.llm_provider import get_llm_assistant_blocked_reason, is_llm_assistant_enabled

	if not is_llm_assistant_enabled():
		frappe.throw(get_llm_assistant_blocked_reason() or _("LLM lyric assistant is disabled."), frappe.ValidationError)
	if (job.transcription_status or "Not Started") != "Completed":
		frappe.throw(_("Completed transcription is required."), frappe.ValidationError)


@frappe.whitelist()
def start_llm_transcript_suggestion(
	job_name: str,
	task: str = "repair_transcript",
	lyrics_text: str | None = None,
	language_hint: str | None = None,
):
	_require_app_access()
	job = _get_job_for_user(job_name)
	_require_llm_assistant(job)

	from audio_stem.integrations.llm_provider import normalize_task_name
	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.llm_assistant_controls import (
		can_start_llm_suggestion,
		enqueue_llm_suggestion,
		llm_queue_is_stale,
	)

	task_name = normalize_task_name(task)
	can_start, blocked_reason = can_start_llm_suggestion(job)
	if not can_start:
		if not (
			(job.llm_suggestion_status or "Not Started") in ("Queued", "Processing")
			and llm_queue_is_stale(job)
		):
			frappe.throw(blocked_reason or _("LLM suggestion cannot be started."), frappe.ValidationError)

	enqueue_llm_suggestion(job, task=task_name, lyrics_text=lyrics_text, language_hint=language_hint)
	log_audit(
		"Start LLM Suggestion",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message=f"Queued LLM suggestion ({task_name}).",
		metadata={"task": task_name},
	)
	job.reload()
	return {**_job_detail_payload(job), **_llm_assistant_payload(job)}


@frappe.whitelist()
def get_llm_suggestion(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	return _llm_assistant_payload(job)


@frappe.whitelist()
def accept_llm_suggestion_as_manual_draft(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	if not _can_edit_transcript(job):
		frappe.throw(_("Transcript editing is not available for this job."), frappe.ValidationError)
	_require_llm_assistant(job)

	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.lyric_assistant import create_manual_draft_from_llm_suggestion

	if (job.llm_suggestion_status or "Not Started") != "Completed":
		frappe.throw(_("A completed LLM suggestion is required before accepting."), frappe.ValidationError)

	result = create_manual_draft_from_llm_suggestion(job)
	log_audit(
		"Accept LLM Suggestion",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message="Accepted LLM suggestion as manual transcript draft.",
	)
	job.reload()
	return {**result, **_manual_transcript_payload(job), **_job_detail_payload(job)}


@frappe.whitelist()
def suggest_scribe_keyterms(job_name: str, lyrics_text: str | None = None, language_hint: str | None = None):
	_require_app_access()
	job = _get_job_for_user(job_name)
	_require_llm_assistant(job)

	from audio_stem.utils.lyric_assistant import suggest_keyterms_from_lyrics

	text = (lyrics_text or job.transcript_text or "").strip()
	if not text:
		frappe.throw(_("Lyrics text is required for keyterm suggestions."), frappe.ValidationError)

	terms = suggest_keyterms_from_lyrics(text, language_hint=language_hint)
	return {"keyterms": terms, "job_name": job.name}


@frappe.whitelist()
def split_lyrics_with_llm(job_name: str, lyrics_text: str | None = None, language_hint: str | None = None):
	_require_app_access()
	job = _get_job_for_user(job_name)
	_require_llm_assistant(job)

	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.llm_assistant_controls import can_start_llm_suggestion, enqueue_llm_suggestion

	can_start, blocked_reason = can_start_llm_suggestion(job)
	if not can_start:
		frappe.throw(blocked_reason or _("LLM suggestion cannot be started."), frappe.ValidationError)

	text = (lyrics_text or job.transcript_text or "").strip()
	if not text:
		frappe.throw(_("Lyrics text is required."), frappe.ValidationError)

	enqueue_llm_suggestion(
		job,
		task="split_lyrics_lines",
		lyrics_text=text,
		language_hint=language_hint,
	)
	log_audit(
		"Start LLM Suggestion",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message="Queued LLM lyric line split.",
		metadata={"task": "split_lyrics_lines"},
	)
	job.reload()
	return {**_job_detail_payload(job), **_llm_assistant_payload(job)}


@frappe.whitelist()
def explain_transcription_quality_with_llm(job_name: str):
	_require_app_access()
	job = _get_job_for_user(job_name)
	_require_llm_assistant(job)

	from audio_stem.utils.audit_log import log_audit
	from audio_stem.utils.llm_assistant_controls import can_start_llm_suggestion, enqueue_llm_suggestion

	can_start, blocked_reason = can_start_llm_suggestion(job)
	if not can_start:
		frappe.throw(blocked_reason or _("LLM suggestion cannot be started."), frappe.ValidationError)

	enqueue_llm_suggestion(job, task="explain_transcription_quality")
	log_audit(
		"Start LLM Suggestion",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message="Queued LLM transcription quality explanation.",
		metadata={"task": "explain_transcription_quality"},
	)
	job.reload()
	return {**_job_detail_payload(job), **_llm_assistant_payload(job)}
