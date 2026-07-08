# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import now_datetime

CANCELLABLE_STATUSES = ("Draft", "Queued", "Uploading", "Processing")
IMMEDIATE_CANCEL_STATUSES = ("Draft", "Queued")
IN_FLIGHT_CANCEL_STATUSES = ("Uploading", "Processing")
TRANSCRIPTION_ACTIVE_STATUSES = ("Queued", "Processing")
KARAOKE_ACTIVE_STATUSES = ("Queued", "Rendering")


def transcription_is_active(job) -> bool:
	from audio_stem.utils.transcription_karaoke_controls import transcription_queue_is_stale

	status = job.get("transcription_status") or "Not Started"
	if status not in TRANSCRIPTION_ACTIVE_STATUSES:
		return False
	return not transcription_queue_is_stale(job)


def karaoke_is_active(job) -> bool:
	from audio_stem.utils.transcription_karaoke_controls import karaoke_queue_is_stale

	status = job.get("karaoke_status") or "Not Started"
	if status not in KARAOKE_ACTIVE_STATUSES:
		return False
	return not karaoke_queue_is_stale(job)


def pipeline_cancel_is_active(job) -> bool:
	return transcription_is_active(job) or karaoke_is_active(job)


def can_cancel_job(job) -> tuple[bool, str | None]:
	if job.status == "Cancelled":
		return False, _("This job is already cancelled.")
	if job.status in CANCELLABLE_STATUSES:
		return True, None
	if pipeline_cancel_is_active(job):
		return True, None
	return False, _("This job cannot be cancelled.")


def finalize_cancelled_job(job, *, cancelled_by: str, cancel_reason: str | None = None, release_credits: bool = True):
	job.status = "Cancelled"
	job.cancellation_requested = 0
	job.cancelled_at = now_datetime()
	job.cancelled_by = cancelled_by
	job.cancel_reason = cancel_reason
	job.completed_at = job.completed_at or now_datetime()
	job.save(ignore_permissions=True)

	if release_credits:
		from audio_stem.integrations.credit_management_client import (
			is_credit_management_enabled,
			release_job_reservation,
		)

		if is_credit_management_enabled() and job.credit_reservation and job.credit_status == "Reserved":
			try:
				release_job_reservation(job, reason=cancel_reason or "Job cancelled")
				job.save(ignore_permissions=True)
			except Exception as exc:
				from audio_stem.utils.errors import safe_error_message

				job.credit_error = safe_error_message(exc)
				if job.credit_status == "Reserved":
					job.credit_status = "Failed"
				job.save(ignore_permissions=True)


def finalize_transcription_cancelled(job, *, message: str | None = None):
	job.reload()
	job.cancellation_requested = 0
	job.transcription_status = "Cancelled"
	job.transcription_error = message or _("Transcription cancelled.")
	job.transcription_completed_at = now_datetime()
	job.save(ignore_permissions=True)


def finalize_karaoke_cancelled(job, *, previous_video=None, previous_ass=None, message: str | None = None):
	job.reload()
	job.cancellation_requested = 0
	if previous_video is not None:
		job.karaoke_video_file = previous_video
	if previous_ass is not None:
		job.karaoke_ass_file = previous_ass
	job.karaoke_status = "Cancelled"
	job.karaoke_error = message or _("Karaoke rendering cancelled.")
	job.karaoke_completed_at = now_datetime()
	job.save(ignore_permissions=True)


def apply_cancel_request(job, *, cancelled_by: str, cancel_reason: str | None = None) -> dict:
	can_cancel, reason = can_cancel_job(job)
	if not can_cancel:
		frappe.throw(reason or _("This job cannot be cancelled."), frappe.ValidationError)

	if job.status in IMMEDIATE_CANCEL_STATUSES:
		finalize_cancelled_job(job, cancelled_by=cancelled_by, cancel_reason=cancel_reason)
		return {
			"cancelled": True,
			"cancellation_requested": False,
			"message": _("Job cancelled."),
		}

	if job.status in IN_FLIGHT_CANCEL_STATUSES:
		job.cancellation_requested = 1
		job.cancel_reason = cancel_reason
		job.cancelled_by = cancelled_by
		job.save(ignore_permissions=True)
		return {
			"cancelled": False,
			"cancellation_requested": True,
			"message": _("Cancellation requested. The current provider job may still finish."),
		}

	if pipeline_cancel_is_active(job):
		job.cancellation_requested = 1
		job.cancel_reason = cancel_reason
		job.cancelled_by = cancelled_by
		job.save(ignore_permissions=True)
		active = []
		if transcription_is_active(job):
			active.append(_("transcription"))
		if karaoke_is_active(job):
			active.append(_("karaoke"))
		return {
			"cancelled": False,
			"cancellation_requested": True,
			"message": _("Cancellation requested for {0}.").format(" / ".join(active)),
		}

	frappe.throw(_("This job cannot be cancelled."), frappe.ValidationError)
	return {}


def should_stop_for_cancellation(job, *, reload: bool = True) -> bool:
	if reload:
		job.reload()
	return job.status == "Cancelled" or bool(job.cancellation_requested)


def cancellation_requested_for_job(job_name: str) -> bool:
	"""Check cancellation without reloading an in-memory job document."""
	row = frappe.db.get_value(
		"Audio Separation Job",
		job_name,
		["status", "cancellation_requested"],
		as_dict=True,
	)
	if not row:
		return False
	return row.status == "Cancelled" or bool(row.cancellation_requested)
