# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import cint, now_datetime
from frappe.utils.background_jobs import is_job_enqueued

from audio_stem.integrations.transcription_provider import (
	get_transcription_provider,
	get_transcription_provider_blocked_reason,
	is_transcription_provider_configured,
)
from audio_stem.utils.limits import get_settings

TRANSCRIPTION_ACTIVE_STATUSES = ("Queued", "Processing")
KARAOKE_ACTIVE_STATUSES = ("Queued", "Rendering")
TRANSCRIPTION_STARTABLE = ("Not Started", "Failed", "Cancelled", "Completed")
KARAOKE_STARTABLE = ("Not Started", "Failed", "Cancelled", "Completed")


def is_karaoke_enabled() -> bool:
	settings = get_settings()
	return bool(cint(settings.karaoke_enabled))


def _default_transcription_status(job) -> str:
	return job.get("transcription_status") or "Not Started"


def _default_karaoke_status(job) -> str:
	return job.get("karaoke_status") or "Not Started"


def _is_stale_queued_status(job, *, job_id_prefix: str, status: str) -> bool:
	return status == "Queued" and not is_job_enqueued(f"{job_id_prefix}:{job.name}")


def transcription_queue_is_stale(job) -> bool:
	status = _default_transcription_status(job)
	return _is_stale_queued_status(job, job_id_prefix="audio_transcription", status=status)


def karaoke_queue_is_stale(job) -> bool:
	status = _default_karaoke_status(job)
	return _is_stale_queued_status(job, job_id_prefix="audio_karaoke", status=status)


def can_start_transcription(job, provider: str | None = None) -> tuple[bool, str | None]:
	settings = get_settings()
	provider = provider or get_transcription_provider(settings)
	if not is_transcription_provider_configured(provider, settings):
		return False, get_transcription_provider_blocked_reason(provider, settings)

	status = _default_transcription_status(job)
	if transcription_queue_is_stale(job):
		return True, None
	if status in TRANSCRIPTION_ACTIVE_STATUSES:
		return False, _("Transcription is already in progress.")
	if status not in TRANSCRIPTION_STARTABLE:
		return False, _("Transcription cannot be started for this job.")
	if job.status in ("Queued", "Uploading", "Processing"):
		return False, _("Wait for audio separation to finish before transcribing.")
	return True, None


def can_start_transcription_source(job, source: str, provider: str | None = None) -> tuple[bool, str | None]:
	can_start, reason = can_start_transcription(job, provider=provider)
	if not can_start:
		return can_start, reason

	source = (source or "Vocal").strip()
	if source == "Vocal":
		if job.status != "Completed":
			return False, _("Vocal transcription requires a completed separation job.")
		if not (job.vocal_file or job.vocal_output_url):
			return False, _("Vocal output is not available yet.")
	elif source == "Original":
		if not job.original_file:
			return False, _("Original audio file is not available.")
	else:
		return False, _("Invalid transcription source.")
	return True, None


def can_start_karaoke(job) -> tuple[bool, str | None]:
	if not is_karaoke_enabled():
		return False, _("Karaoke rendering is disabled.")

	settings = get_settings()
	if not cint(settings.karaoke_ass_enabled):
		return False, _("Karaoke ASS subtitle generation is disabled.")

	status = _default_karaoke_status(job)
	if karaoke_queue_is_stale(job):
		return True, None
	if status in KARAOKE_ACTIVE_STATUSES:
		return False, _("Karaoke rendering is already in progress.")
	if status not in KARAOKE_STARTABLE:
		return False, _("Karaoke rendering cannot be started for this job.")
	if (job.transcription_status or "Not Started") != "Completed":
		return False, _("Completed transcription is required before karaoke rendering.")
	return True, None


def enqueue_transcription(
	job,
	*,
	source: str,
	language: str | None = None,
	prompt: str | None = None,
	provider: str | None = None,
	scribe_model: str | None = None,
	keyterms: str | None = None,
	no_verbatim: int | None = None,
	tag_audio_events: int | None = None,
	diarize: int | None = None,
):
	if (job.transcription_status or "Not Started") == "Completed":
		from audio_stem.utils.downstream_assets import mark_transcription_retry_stale

		mark_transcription_retry_stale(job)

	job.transcription_status = "Queued"
	job.transcription_source = source
	job.transcription_error = None
	job.transcription_quality_warning = None
	job.transcription_word_count = 0
	job.transcription_segment_count = 0
	job.transcription_detected_language = None
	job.transcription_first_segment_start = None
	job.transcription_bad_timestamp_count = 0
	job.transcription_language_probability = None
	job.transcription_keyterms_used = None
	job.transcription_provider_warning = None
	job.save(ignore_permissions=True)
	frappe.enqueue(
		"audio_stem.workers.transcription_worker.process_transcription",
		queue="long",
		job_id=f"audio_transcription:{job.name}",
		name=job.name,
		source=source,
		language=language,
		prompt=prompt,
		provider=provider,
		scribe_model=scribe_model,
		keyterms=keyterms,
		no_verbatim=no_verbatim,
		tag_audio_events=tag_audio_events,
		diarize=diarize,
	)


def enqueue_karaoke(job, *, template: str | None = None):
	settings = get_settings()
	from audio_stem.utils.karaoke_subtitles import resolve_karaoke_style_preset

	style_preset = resolve_karaoke_style_preset(template)
	job.karaoke_status = "Queued"
	job.karaoke_template = style_preset
	job.karaoke_error = None
	job.save(ignore_permissions=True)
	frappe.enqueue(
		"audio_stem.workers.karaoke_worker.process_karaoke_render",
		queue="long",
		job_id=f"audio_karaoke:{job.name}",
		name=job.name,
		template=style_preset,
	)
