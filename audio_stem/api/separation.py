# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os

import frappe
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.audio import get_audio_duration_seconds
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


def _get_display_currency() -> str:
	currency = frappe.db.get_single_value("Audio Separation Settings", "display_currency")
	return currency or DEFAULT_DISPLAY_CURRENCY


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


def _get_attached_file_doc(file_url: str):
	file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not file_name:
		frappe.throw(_("Uploaded file not found"))
	return frappe.get_doc("File", file_name)


def _can_start_job(job, settings=None) -> tuple[bool, str | None]:
	settings = settings or get_settings()

	if not cint(settings.enabled):
		return False, _("Audio separation is disabled in Audio Separation Settings.")

	if job.status in ACTIVE_STATUSES:
		return False, _("This job is already running.")

	if job.status == "Completed":
		return False, _("This job is already completed.")

	if job.status not in STARTABLE_STATUSES:
		return False, _("This job cannot be started.")

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

	return True, None


def _job_payload(job):
	can_start, blocked_reason = _can_start_job(job)
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
		"estimated_cost_usd": calculate_provider_cost(job.duration_seconds),
		"display_currency": _get_display_currency(),
		"can_start": can_start,
		"start_blocked_reason": blocked_reason,
		"is_active": job.status in ACTIVE_STATUSES,
	}


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
	limits = get_limits_payload()
	return {
		**limits,
		"display_currency": _get_display_currency(),
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
	settings = get_settings()

	if job.status in ACTIVE_STATUSES:
		return {
			"status": job.status,
			"name": job.name,
			"already_active": True,
			"provider_cost_usd": flt(job.provider_cost_usd),
		}

	if job.status == "Completed":
		frappe.throw(_("This job is already completed."))

	if job.status not in STARTABLE_STATUSES:
		frappe.throw(_("Job can only be started from Draft or Failed status."))

	ensure_enabled(settings)

	if not job.original_file:
		frappe.throw(_("Please attach an audio file before starting separation."))

	file_doc = _get_attached_file_doc(job.original_file)
	validate_file_size(file_doc, settings)
	validate_duration(job.duration_seconds, settings, require_duration=True)

	if not _is_system_manager():
		ensure_single_active_job(job.user, exclude_job_name=job.name)

	job.provider_cost_usd = calculate_provider_cost(job.duration_seconds, settings)
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

	return {
		"status": job.status,
		"name": job.name,
		"provider_cost_usd": flt(job.provider_cost_usd),
		"already_active": False,
	}
