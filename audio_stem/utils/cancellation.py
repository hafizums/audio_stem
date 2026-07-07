# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import now_datetime

CANCELLABLE_STATUSES = ("Draft", "Queued", "Uploading", "Processing")
IMMEDIATE_CANCEL_STATUSES = ("Draft", "Queued")
IN_FLIGHT_CANCEL_STATUSES = ("Uploading", "Processing")


def can_cancel_job(job) -> tuple[bool, str | None]:
	if job.status == "Cancelled":
		return False, _("This job is already cancelled.")
	if job.status not in CANCELLABLE_STATUSES:
		return False, _("This job cannot be cancelled.")
	return True, None


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

	job.cancellation_requested = 1
	job.cancel_reason = cancel_reason
	job.cancelled_by = cancelled_by
	job.save(ignore_permissions=True)
	return {
		"cancelled": False,
		"cancellation_requested": True,
		"message": _(
			"Cancellation requested. The current provider job may still finish."
		),
	}


def should_stop_for_cancellation(job) -> bool:
	job.reload()
	return job.status == "Cancelled" or bool(job.cancellation_requested)
