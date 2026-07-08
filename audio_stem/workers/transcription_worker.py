# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import traceback

import tempfile

import frappe
from frappe.utils import flt, now_datetime

from audio_stem.integrations.transcription_provider import (
	PROVIDER_ELEVENLABS,
	PROVIDER_OPENAI,
	get_transcription_provider,
	resolve_transcription_provider,
	transcribe_audio,
)
from audio_stem.utils.audit_log import log_audit
from audio_stem.utils.cancellation import (
	cancellation_requested_for_job,
	finalize_transcription_cancelled,
	should_stop_for_cancellation,
)
from audio_stem.utils.downstream_assets import clear_downstream_stale_after_transcription_complete
from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.limits import get_settings
from audio_stem.utils.scribe_keyterms import parse_keyterms
from audio_stem.utils.transcription_assets import (
	cleanup_temp_path,
	estimate_transcription_cost,
	prepare_audio_for_whisper,
	resolve_transcription_source_path,
	write_raw_provider_json,
	write_srt_from_segments_or_words,
	write_transcript_json,
	write_vtt_from_segments_or_words,
)
from audio_stem.utils.transcription_quality import (
	apply_transcription_quality_diagnostics,
	resolve_transcription_language,
)


def process_transcription(
	name: str,
	source: str = "Vocal",
	language: str | None = None,
	prompt: str | None = None,
	provider: str | None = None,
	scribe_model: str | None = None,
	keyterms: str | None = None,
	no_verbatim: int | None = None,
	tag_audio_events: int | None = None,
	diarize: int | None = None,
):
	job = frappe.get_doc("Audio Separation Job", name)
	settings = get_settings()
	selected_provider = resolve_transcription_provider(provider, settings)

	if should_stop_for_cancellation(job):
		finalize_transcription_cancelled(job)
		log_audit(
			"Fail Transcription",
			reference_doctype=job.doctype,
			reference_name=job.name,
			message="Transcription cancelled.",
		)
		return

	resolved_language = resolve_transcription_language(language, settings)
	parsed_keyterms = parse_keyterms(keyterms) if keyterms else []

	job.reload()
	job.transcription_status = "Processing"
	job.transcription_source = source
	job.transcription_started_at = now_datetime()
	job.transcription_error = None
	job.transcription_quality_warning = None
	job.transcription_provider_warning = None
	job.transcription_word_count = 0
	job.transcription_segment_count = 0
	job.transcription_detected_language = None
	job.transcription_first_segment_start = None
	job.transcription_bad_timestamp_count = 0
	job.transcription_language_probability = None
	job.transcription_keyterms_used = None
	job.transcription_provider = selected_provider
	if selected_provider == PROVIDER_OPENAI:
		job.transcription_model = settings.transcription_model or "whisper-1"
		job.transcription_provider_model = job.transcription_model
	elif selected_provider == PROVIDER_ELEVENLABS:
		job.transcription_model = scribe_model or settings.elevenlabs_scribe_model or "scribe_v2"
		job.transcription_provider_model = job.transcription_model
	job.transcription_language = resolved_language
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

		transcript_data = transcribe_audio(
			prepared_path,
			language=resolved_language,
			prompt=prompt if selected_provider == PROVIDER_OPENAI else None,
			keyterms=parsed_keyterms,
			provider=selected_provider,
			scribe_model=scribe_model,
			no_verbatim=bool(no_verbatim) if no_verbatim is not None else None,
			tag_audio_events=bool(tag_audio_events) if tag_audio_events is not None else None,
			diarize=bool(diarize) if diarize is not None else None,
		)

		if cancellation_requested_for_job(job.name):
			finalize_transcription_cancelled(job)
			log_audit(
				"Fail Transcription",
				reference_doctype=job.doctype,
				reference_name=job.name,
				message="Transcription cancelled after provider returned.",
			)
			return

		raw_response = transcript_data.get("raw_response_dict")
		if raw_response:
			write_raw_provider_json(job, raw_response)

		job.transcript_text = transcript_data.get("text")
		job.transcription_provider = transcript_data.get("provider") or selected_provider
		job.transcription_provider_model = transcript_data.get("model") or job.transcription_provider_model
		job.transcription_model = job.transcription_provider_model
		if transcript_data.get("language_probability") is not None:
			job.transcription_language_probability = flt(transcript_data.get("language_probability"))
		if transcript_data.get("keyterms_used"):
			job.transcription_keyterms_used = ", ".join(transcript_data.get("keyterms_used"))

		write_transcript_json(job, transcript_data)
		write_srt_from_segments_or_words(job, transcript_data)
		write_vtt_from_segments_or_words(job, transcript_data)
		apply_transcription_quality_diagnostics(
			job,
			transcript_data,
			requested_language=resolved_language,
		)
		job.transcription_cost_usd = estimate_transcription_cost(
			transcript_data.get("duration") or job.duration_seconds,
			provider=selected_provider,
			keyterms_used=bool(parsed_keyterms or transcript_data.get("keyterms_used")),
		)
		job.transcription_status = "Completed"
		job.transcription_completed_at = now_datetime()
		clear_downstream_stale_after_transcription_complete(job)
		job.save(ignore_permissions=True)
		log_audit(
			"Complete Transcription",
			reference_doctype=job.doctype,
			reference_name=job.name,
			message=f"Transcription completed via {job.transcription_provider}.",
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
