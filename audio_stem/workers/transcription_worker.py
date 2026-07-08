# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import traceback

import tempfile

import frappe
from frappe.utils import now_datetime

from audio_stem.integrations.openai_transcription_client import transcribe_with_whisper
from audio_stem.utils.audit_log import log_audit
from audio_stem.utils.cancellation import (
	cancellation_requested_for_job,
	finalize_transcription_cancelled,
	should_stop_for_cancellation,
)
from audio_stem.utils.downstream_assets import clear_downstream_stale_after_transcription_complete
from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.limits import get_settings
from audio_stem.utils.transcription_assets import (
	cleanup_temp_path,
	estimate_transcription_cost,
	prepare_audio_for_whisper,
	resolve_transcription_source_path,
	write_srt_from_segments_or_words,
	write_transcript_json,
	write_vtt_from_segments_or_words,
)


def process_transcription(name: str, source: str = "Vocal", language: str | None = None):
	job = frappe.get_doc("Audio Separation Job", name)
	settings = get_settings()

	if should_stop_for_cancellation(job):
		finalize_transcription_cancelled(job)
		log_audit(
			"Fail Transcription",
			reference_doctype=job.doctype,
			reference_name=job.name,
			message="Transcription cancelled.",
		)
		return

	job.reload()
	job.transcription_status = "Processing"
	job.transcription_source = source
	job.transcription_started_at = now_datetime()
	job.transcription_error = None
	job.transcription_model = settings.transcription_model or "whisper-1"
	job.transcription_language = language or settings.default_transcription_language
	job.save(ignore_permissions=True)

	temp_paths = []

	try:
		source_path = resolve_transcription_source_path(job, source)
		if source_path.startswith(tempfile.gettempdir()):
			temp_paths.append(source_path)
		prepared_path, should_cleanup_prepared = prepare_audio_for_whisper(source_path)
		if should_cleanup_prepared:
			temp_paths.append(prepared_path)

		if cancellation_requested_for_job(job.name):
			finalize_transcription_cancelled(job)
			log_audit(
				"Fail Transcription",
				reference_doctype=job.doctype,
				reference_name=job.name,
				message="Transcription cancelled before provider call.",
			)
			return

		transcript_data = transcribe_with_whisper(prepared_path, language=language)

		if cancellation_requested_for_job(job.name):
			finalize_transcription_cancelled(job)
			log_audit(
				"Fail Transcription",
				reference_doctype=job.doctype,
				reference_name=job.name,
				message="Transcription cancelled after provider returned.",
			)
			return

		job.transcript_text = transcript_data.get("text")
		write_transcript_json(job, transcript_data)
		write_srt_from_segments_or_words(job, transcript_data)
		write_vtt_from_segments_or_words(job, transcript_data)
		job.transcription_cost_usd = estimate_transcription_cost(
			transcript_data.get("duration") or job.duration_seconds
		)
		job.transcription_status = "Completed"
		job.transcription_completed_at = now_datetime()
		clear_downstream_stale_after_transcription_complete(job)
		job.save(ignore_permissions=True)
		log_audit(
			"Complete Transcription",
			reference_doctype=job.doctype,
			reference_name=job.name,
			message="Transcription completed.",
		)
	except Exception as exc:
		job.reload()
		if should_stop_for_cancellation(job):
			finalize_transcription_cancelled(job)
			log_audit(
				"Fail Transcription",
				reference_doctype=job.doctype,
				reference_name=job.name,
				message="Transcription cancelled.",
			)
			return
		job.transcription_status = "Failed"
		job.transcription_error = safe_error_message(exc)
		job.transcription_completed_at = now_datetime()
		job.save(ignore_permissions=True)
		frappe.log_error(title=f"Transcription failed for {job.name}", message=traceback.format_exc())
		log_audit(
			"Fail Transcription",
			reference_doctype=job.doctype,
			reference_name=job.name,
			message=job.transcription_error,
		)
		raise
	finally:
		for path in temp_paths:
			cleanup_temp_path(path, should_cleanup=True)
