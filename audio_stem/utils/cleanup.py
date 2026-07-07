# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os

import frappe
from frappe import _
from frappe.utils import add_days, cint, now_datetime
from frappe.utils.file_manager import get_file_path

from audio_stem.utils.limits import get_settings

TERMINAL_STATUSES = ("Completed", "Failed", "Cancelled")


def cleanup_old_audio_jobs():
	settings = get_settings()
	if not cint(settings.cleanup_enabled):
		return {"processed": 0, "skipped": True}

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
		if _cleanup_job(job, settings):
			processed += 1

	return {"processed": processed, "skipped": False}


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
		for field in ("vocal_file", "instrumental_file"):
			file_url = getattr(job, field, None)
			if file_url and _delete_attached_file(file_url):
				setattr(job, field, None)
				changed = True

		if job.vocal_file is None and job.instrumental_file is None:
			if job.vocal_output_url:
				job.vocal_output_url = None
				changed = True
			if job.instrumental_output_url:
				job.instrumental_output_url = None
				changed = True

		if changed:
			notes.append(_("Output files removed after retention."))

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


def _delete_attached_file(file_url: str) -> bool:
	if not file_url:
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
