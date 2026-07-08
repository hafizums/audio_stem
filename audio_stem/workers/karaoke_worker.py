# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import traceback

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from audio_stem.utils.audit_log import log_audit
from audio_stem.utils.cancellation import should_stop_for_cancellation
from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.karaoke_subtitles import (
	build_karaoke_ass_with_engine,
	build_karaoke_words_json,
	get_karaoke_engine_version,
	render_karaoke_video_with_engine,
	resolve_karaoke_style_preset,
	write_karaoke_json,
)
from audio_stem.utils.limits import get_settings
from audio_stem.utils.transcript_corrections import load_karaoke_transcript_data


def process_karaoke_render(name: str, template: str | None = None):
	job = frappe.get_doc("Audio Separation Job", name)
	settings = get_settings()
	previous_video = job.karaoke_video_file
	previous_ass = job.karaoke_ass_file
	style_preset = resolve_karaoke_style_preset(template, job=job)

	if should_stop_for_cancellation(job):
		job.karaoke_status = "Cancelled"
		job.karaoke_completed_at = now_datetime()
		job.save(ignore_permissions=True)
		log_audit("Fail Karaoke", reference_doctype=job.doctype, reference_name=job.name, message="Karaoke cancelled.")
		return

	job.reload()
	job.karaoke_status = "Rendering"
	job.karaoke_template = style_preset
	job.karaoke_started_at = now_datetime()
	job.karaoke_error = None
	job.save(ignore_permissions=True)

	video_render_enabled = bool(cint(settings.karaoke_video_render_enabled))
	ass_generated = False
	video_generated = False

	try:
		transcript_data = load_karaoke_transcript_data(job)
		karaoke_data = build_karaoke_words_json(job, transcript_data)
		write_karaoke_json(job, karaoke_data)
		job.save(ignore_permissions=True)

		build_karaoke_ass_with_engine(job, style_preset=style_preset)
		ass_generated = True
		job.karaoke_engine_version = get_karaoke_engine_version()
		job.save(ignore_permissions=True)

		if video_render_enabled:
			render_karaoke_video_with_engine(job, style_preset=style_preset)
			video_generated = True
			job.karaoke_error = None
		else:
			job.karaoke_error = _("ASS subtitle generated. Video render is disabled.")

		job.karaoke_status = "Completed"
		job.karaoke_completed_at = now_datetime()
		job.save(ignore_permissions=True)
		log_audit(
			"Complete Karaoke",
			reference_doctype=job.doctype,
			reference_name=job.name,
			message="Karaoke ASS generated."
			if not video_render_enabled
			else "Karaoke ASS and video generated.",
		)
	except Exception as exc:
		job.reload()
		if previous_video:
			job.karaoke_video_file = previous_video
		if previous_ass and not ass_generated:
			job.karaoke_ass_file = previous_ass

		if ass_generated and video_render_enabled and not video_generated:
			job.karaoke_status = "Completed"
			job.karaoke_error = _("ASS subtitle generated. Video render failed.")
		else:
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
		if job.karaoke_status == "Failed":
			raise
