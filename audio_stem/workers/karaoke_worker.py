# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json
import os
import traceback

import frappe
from frappe.utils import now_datetime

from audio_stem.utils.audit_log import log_audit
from audio_stem.utils.cancellation import should_stop_for_cancellation
from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.files import resolve_frappe_file_path
from audio_stem.utils.karaoke_subtitles import (
	build_karaoke_words_json,
	create_plain_lyrics_video,
	render_karaoke_video_with_pycaps,
	write_karaoke_json,
)
from audio_stem.utils.transcription_assets import write_transcript_json


def _load_transcript_data(job) -> dict:
	if job.transcript_json_file:
		path = resolve_frappe_file_path(job.transcript_json_file)
		if path and os.path.exists(path):
			with open(path, encoding="utf-8") as handle:
				return json.load(handle)
	return {
		"text": job.transcript_text,
		"language": job.transcription_language,
		"duration": job.duration_seconds,
		"segments": [],
		"words": [],
	}


def process_karaoke_render(name: str, template: str | None = None):
	job = frappe.get_doc("Audio Separation Job", name)
	previous_video = job.karaoke_video_file

	if should_stop_for_cancellation(job):
		job.karaoke_status = "Cancelled"
		job.karaoke_completed_at = now_datetime()
		job.save(ignore_permissions=True)
		log_audit("Fail Karaoke", reference_doctype=job.doctype, reference_name=job.name, message="Karaoke cancelled.")
		return

	job.reload()
	job.karaoke_status = "Rendering"
	job.karaoke_template = template or job.karaoke_template
	job.karaoke_started_at = now_datetime()
	job.karaoke_error = None
	job.save(ignore_permissions=True)

	input_video_path = None
	karaoke_json_path = None

	try:
		transcript_data = _load_transcript_data(job)
		karaoke_data = build_karaoke_words_json(job, transcript_data)
		write_karaoke_json(job, karaoke_data)
		karaoke_json_path = resolve_frappe_file_path(job.karaoke_subtitle_json_file)

		input_video_path = create_plain_lyrics_video(job)
		new_video_url = render_karaoke_video_with_pycaps(
			job,
			input_video_path=input_video_path,
			karaoke_json_path=karaoke_json_path,
			template=template,
		)
		job.karaoke_video_file = new_video_url
		job.karaoke_status = "Completed"
		job.karaoke_completed_at = now_datetime()
		job.save(ignore_permissions=True)
		log_audit(
			"Complete Karaoke",
			reference_doctype=job.doctype,
			reference_name=job.name,
			message="Karaoke render completed.",
		)
	except Exception as exc:
		job.reload()
		if previous_video:
			job.karaoke_video_file = previous_video
		job.karaoke_status = "Failed"
		job.karaoke_error = safe_error_message(exc)
		job.karaoke_completed_at = now_datetime()
		job.save(ignore_permissions=True)
		frappe.log_error(title=f"Karaoke render failed for {job.name}", message=traceback.format_exc())
		log_audit(
			"Fail Karaoke",
			reference_doctype=job.doctype,
			reference_name=job.name,
			message=job.karaoke_error,
		)
		raise
	finally:
		if input_video_path and os.path.exists(input_video_path):
			try:
				os.unlink(input_video_path)
			except OSError:
				pass
