# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os

import frappe
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.audio import get_audio_duration_seconds

PROVIDER = "WaveSpeed"
PROVIDER_MODEL = "wavespeed-ai/audio-vocal-isolator"


def _is_system_manager() -> bool:
	return frappe.session.user == "Administrator" or "System Manager" in frappe.get_roles()


def _require_login():
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required"), frappe.PermissionError)


def _get_job_for_user(job_name: str):
	if not frappe.db.exists("Audio Separation Job", job_name):
		frappe.throw(_("Job not found"), frappe.DoesNotExistError)

	owner = frappe.db.get_value("Audio Separation Job", job_name, "user")
	if not _is_system_manager() and owner != frappe.session.user:
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	return frappe.get_doc("Audio Separation Job", job_name)


def _job_payload(job):
	return {
		"name": job.name,
		"status": job.status,
		"original_file": job.original_file,
		"original_filename": job.original_filename,
		"vocal_output_url": job.vocal_output_url,
		"instrumental_output_url": job.instrumental_output_url,
		"error_message": job.error_message,
		"duration_seconds": cint(job.duration_seconds),
		"provider_cost_usd": flt(job.provider_cost_usd),
		"estimated_cost_usd": _estimate_cost(job.duration_seconds),
	}


def _estimate_cost(duration_seconds):
	settings = frappe.get_single("Audio Separation Settings")
	duration = cint(duration_seconds)
	if not duration:
		return None
	return flt(duration) * flt(settings.cost_per_second_usd)


def _resolve_original_filename(file_url: str) -> str | None:
	file_name = frappe.db.get_value("File", {"file_url": file_url}, "file_name")
	if file_name:
		return file_name
	return os.path.basename(file_url) if file_url else None


@frappe.whitelist()
def create_job_from_file(file_url: str):
	_require_login()

	if not file_url:
		frappe.throw(_("file_url is required"))

	file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not file_name:
		frappe.throw(_("Uploaded file not found"))

	file_doc = frappe.get_doc("File", file_name)

	duration_seconds = None
	try:
		duration_seconds = get_audio_duration_seconds(file_doc.get_full_path())
	except Exception:
		duration_seconds = None

	job = frappe.get_doc(
		{
			"doctype": "Audio Separation Job",
			"user": frappe.session.user,
			"status": "Draft",
			"original_file": file_url,
			"original_filename": _resolve_original_filename(file_url),
			"duration_seconds": duration_seconds,
		}
	)
	job.insert(ignore_permissions=True)

	return _job_payload(job)


@frappe.whitelist()
def get_job_status(job_name: str):
	_require_login()
	job = _get_job_for_user(job_name)
	return _job_payload(job)


@frappe.whitelist()
def get_page_settings():
	_require_login()
	return {
		"enabled": cint(frappe.db.get_single_value("Audio Separation Settings", "enabled")),
		"cost_per_second_usd": flt(frappe.db.get_single_value("Audio Separation Settings", "cost_per_second_usd")),
	}


@frappe.whitelist()
def get_recent_jobs(limit=10):
	_require_login()
	limit = min(cint(limit) or 10, 50)

	filters = {"user": frappe.session.user}

	jobs = frappe.get_all(
		"Audio Separation Job",
		filters=filters,
		fields=[
			"name",
			"status",
			"creation",
			"duration_seconds",
			"vocal_output_url",
			"instrumental_output_url",
		],
		order_by="creation desc",
		limit=limit,
		ignore_permissions=True,
	)
	return jobs


@frappe.whitelist()
def start_separation(job_name: str):
	_require_login()
	job = _get_job_for_user(job_name)

	if job.status not in ("Draft", "Failed"):
		frappe.throw(_("Job can only be started from Draft or Failed status."))

	if not job.original_file:
		frappe.throw(_("Please attach an audio file before starting separation."))

	settings = frappe.get_single("Audio Separation Settings")
	if not settings.enabled:
		frappe.throw(_("Audio separation is disabled in Audio Separation Settings."))

	job.status = "Queued"
	job.provider = PROVIDER
	job.provider_model = PROVIDER_MODEL
	job.error_message = None
	job.vocal_output_url = None
	job.instrumental_output_url = None
	job.vocal_file = None
	job.instrumental_file = None
	job.started_at = None
	job.completed_at = None
	job.save(ignore_permissions=True)

	frappe.enqueue(
		"audio_stem.workers.separation_worker.process_audio_separation",
		queue="long",
		job_id=f"audio_separation:{job.name}",
		name=job.name,
	)

	return {"status": job.status, "name": job.name}
