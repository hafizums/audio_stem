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


def _job_payload(job):
	can_start, blocked_reason = _can_start_job(job)
	can_retry, retry_blocked_reason = _can_retry_job(job)
	credit_enabled = _credit_settings_flag_enabled()
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
		"duration_seconds": cint(job.duration_seconds),
		"provider_cost_usd": flt(job.provider_cost_usd),
		"estimated_cost_usd": calculate_provider_cost(job.duration_seconds),
		"display_currency": _get_display_currency(),
		"completed_at": job.completed_at,
		"creation": job.creation,
		"can_start": can_start,
		"start_blocked_reason": blocked_reason,
		"can_retry": can_retry,
		"retry_blocked_reason": retry_blocked_reason,
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
	_require_login()

	files = frappe.request.files
	if "file" not in files:
		frappe.throw(_("No file uploaded"))

	return _save_uploaded_audio(files["file"])


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

	return _job_payload(job)


@frappe.whitelist()
def get_job_status(job_name: str):
	_require_login()
	job = _get_job_for_user(job_name)
	return _job_payload(job)


@frappe.whitelist()
def get_page_settings():
	_require_login()
	from audio_stem.integrations.credit_management_client import get_audio_credit_type, is_credit_management_enabled

	limits = get_limits_payload()
	return {
		**limits,
		"display_currency": _get_display_currency(),
		"credit_management_enabled": is_credit_management_enabled(),
		"credit_type": get_audio_credit_type() if is_credit_management_enabled() else None,
	}


@frappe.whitelist()
def get_my_credit_balance():
	_require_login()
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
	_require_login()
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
		if job.credit_status in ("Released", "Failed"):
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
	_require_login()
	job = _get_job_for_user(job_name)
	settings = get_settings()

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
	return {**_job_payload(job), "already_active": False}


@frappe.whitelist()
def retry_failed_job(job_name: str):
	_require_login()
	job = _get_job_for_user(job_name)
	settings = get_settings()

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
	return {**_job_payload(job), "already_active": False}


@frappe.whitelist()
def create_job_zip(job_name: str):
	_require_login()
	job = _get_job_for_user(job_name)

	if job.status != "Completed":
		frappe.throw(_("ZIP download is only available for completed jobs."), frappe.ValidationError)

	from audio_stem.utils.zip_download import create_job_zip_file

	zip_url = create_job_zip_file(job)
	job.zip_file = zip_url
	job.save(ignore_permissions=True)
	return {"zip_file": zip_url, "job_name": job.name}
