# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os

import frappe
from frappe import _
from frappe.utils import add_days, cint, now_datetime
from frappe.utils.file_manager import get_file_path

from audio_stem.utils.limits import get_settings

TERMINAL_STATUSES = ("Completed", "Failed", "Cancelled")
ACTIVE_PIPELINE_STATUSES = ("Queued", "Uploading", "Processing")
ACTIVE_TRANSCRIPTION_STATUSES = ("Queued", "Processing")
ACTIVE_KARAOKE_STATUSES = ("Queued", "Rendering")


def cleanup_old_audio_jobs():
	settings = get_settings()
	if not cint(settings.cleanup_enabled):
		return {"processed": 0, "skipped": True, "audit_logs_deleted": 0}

	retention_days = cint(settings.retention_days) or 7
	cutoff = add_days(now_datetime(), -retention_days)

	jobs = frappe.get_all(
		"Audio Separation Job",
		filters={
			"status": ("in", list(TERMINAL_STATUSES)),
			"modified": ("<", cutoff),
		},
		fields=["name"],
		limit=500,
		ignore_permissions=True,
	)

	processed = 0
	for row in jobs:
		job = frappe.get_doc("Audio Separation Job", row.name)
		if _job_has_active_pipeline(job):
			continue
		if _cleanup_job(job, settings):
			processed += 1

	audit_logs_deleted = cleanup_old_audit_logs(settings)
	return {"processed": processed, "skipped": False, "audit_logs_deleted": audit_logs_deleted}


def cleanup_old_audit_logs(settings=None) -> int:
	settings = settings or get_settings()
	retention_days = cint(settings.audit_log_retention_days) or 0
	if retention_days <= 0:
		return 0

	cutoff = add_days(now_datetime(), -retention_days)
	rows = frappe.get_all(
		"Audio Stem Audit Log",
		filters={"created_at": ("<", cutoff)},
		fields=["name"],
		order_by="created_at asc",
		limit=1000,
		ignore_permissions=True,
	)
	deleted = 0
	frappe.flags.ignore_audit_log_delete = True
	try:
		for row in rows:
			try:
				frappe.delete_doc("Audio Stem Audit Log", row.name, ignore_permissions=True, force=True)
				deleted += 1
			except Exception:
				frappe.log_error(title=f"Failed to delete audit log {row.name}")
	finally:
		frappe.flags.ignore_audit_log_delete = False
	return deleted


def _job_has_active_pipeline(job) -> bool:
	if job.status in ACTIVE_PIPELINE_STATUSES:
		return True
	if (job.transcription_status or "Not Started") in ACTIVE_TRANSCRIPTION_STATUSES:
		return True
	if (job.karaoke_status or "Not Started") in ACTIVE_KARAOKE_STATUSES:
		return True
	return False


def _cleanup_job(job, settings) -> bool:
	notes = []
	changed = False

	if cint(settings.delete_original_after_completion) and job.status == "Completed" and job.original_file:
		if _delete_attached_file(job.original_file):
			notes.append(_("Original file removed."))
			job.original_file = None
			changed = True
		else:
			notes.append(_("Original file already removed or unavailable."))

	if cint(settings.delete_outputs_after_retention):
		output_fields_deleted = False
		for field in ("vocal_file", "instrumental_file"):
			file_url = getattr(job, field, None)
			if file_url and _delete_attached_file(file_url):
				setattr(job, field, None)
				changed = True
				output_fields_deleted = True

		for field in (
			"karaoke_ass_file",
			"karaoke_video_file",
			"karaoke_subtitle_json_file",
			"karaoke_render_source_video_file",
		):
			file_url = getattr(job, field, None)
			if file_url and _delete_attached_file(file_url):
				setattr(job, field, None)
				changed = True
				output_fields_deleted = True

		if job.vocal_file is None and job.instrumental_file is None:
			if job.vocal_output_url:
				job.vocal_output_url = None
				changed = True
				output_fields_deleted = True
			if job.instrumental_output_url:
				job.instrumental_output_url = None
				changed = True
				output_fields_deleted = True

		if output_fields_deleted:
			notes.append(_("Output files removed after retention."))

	if cint(settings.delete_transcripts_after_retention):
		if _delete_job_file_fields(
			job,
			("transcript_json_file", "transcript_srt_file", "transcript_vtt_file"),
		):
			changed = True
			notes.append(_("Transcript files removed after retention."))

	if cint(settings.delete_manual_transcripts_after_retention):
		if _delete_job_file_fields(
			job,
			(
				"manual_transcript_json_file",
				"manual_transcript_srt_file",
				"manual_transcript_vtt_file",
			),
		):
			changed = True
			notes.append(_("Manual transcript files removed after retention."))

	if cint(settings.delete_zip_after_retention) and job.zip_file:
		if _delete_attached_file(job.zip_file):
			job.zip_file = None
			changed = True
			notes.append(_("ZIP file removed after retention."))

	if job.get("karaoke_background_video_file"):
		default_bg = settings.get("default_karaoke_background_video")
		if job.karaoke_background_video_file != default_bg:
			notes.append(_("Job background video preserved after retention cleanup."))
			changed = True

	if notes:
		existing = (job.cleanup_notes or "").strip()
		new_note = " ".join(str(note) for note in notes)
		job.cleanup_notes = f"{existing} {new_note}".strip() if existing else new_note
		changed = True

	if changed:
		job.save(ignore_permissions=True)
		from audio_stem.utils.audit_log import log_audit

		log_audit(
			"Cleanup",
			reference_doctype=job.doctype,
			reference_name=job.name,
			message="Cleanup removed files for job.",
			user=job.user,
		)

	return changed


def _delete_job_file_fields(job, fieldnames: tuple[str, ...]) -> bool:
	deleted_any = False
	for field in fieldnames:
		file_url = getattr(job, field, None)
		if file_url and _delete_attached_file(file_url):
			setattr(job, field, None)
			deleted_any = True
	return deleted_any


def _delete_attached_file(file_url: str) -> bool:
	if not file_url:
		return False

	settings = get_settings()
	default_bg = settings.get("default_karaoke_background_video")
	if default_bg and file_url == default_bg:
		return False

	file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not file_name:
		return False

	file_doc = frappe.get_doc("File", file_name)
	file_path = file_doc.get_full_path()
	if file_path and os.path.exists(file_path):
		try:
			os.remove(file_path)
		except OSError:
			pass

	try:
		file_doc.delete(ignore_permissions=True)
		return True
	except Exception:
		frappe.log_error(title=f"Failed to delete file {file_name}")
		return False
