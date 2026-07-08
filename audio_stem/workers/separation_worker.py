# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import traceback

import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe.utils.file_manager import get_file_path

from audio_stem.integrations.wavespeed_client import isolate_vocal_and_instrumental
from audio_stem.utils.audit_log import log_audit
from audio_stem.utils.cancellation import finalize_cancelled_job, should_stop_for_cancellation
from audio_stem.utils.credit_reconciliation import CREDIT_RECONCILIATION_STATUS, is_credit_reconciliation_needed
from audio_stem.utils.downstream_assets import invalidate_downstream_assets, job_had_downstream_assets
from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.limits import calculate_provider_cost, get_settings
from audio_stem.utils.output_storage import maybe_store_outputs_locally


def _consume_job_credits_if_needed(job):
	from audio_stem.integrations.credit_management_client import (
		consume_job_reservation,
		is_credit_management_enabled,
	)

	if not is_credit_management_enabled() or not job.credit_reservation:
		return

	if job.credit_status == "Consumed":
		return

	try:
		consume_job_reservation(job)
		job.save(ignore_permissions=True)
	except Exception as exc:
		job.credit_status = CREDIT_RECONCILIATION_STATUS
		job.credit_error = safe_error_message(exc)
		job.save(ignore_permissions=True)
		frappe.log_error(
			title=f"Credit consume failed for completed job {job.name}",
			message=traceback.format_exc(),
		)


def _release_job_credits_if_needed(job, reason: str | None = None):
	from audio_stem.integrations.credit_management_client import (
		is_credit_management_enabled,
		release_job_reservation,
	)

	if not is_credit_management_enabled() or not job.credit_reservation:
		return

	if job.credit_status in ("Released", "Consumed", "Not Required"):
		return

	if job.credit_status == "Failed":
		return

	try:
		release_job_reservation(job, reason=reason)
		job.save(ignore_permissions=True)
	except Exception as exc:
		job.credit_error = safe_error_message(exc)
		if job.credit_status == "Reserved":
			job.credit_status = "Failed"
		job.save(ignore_permissions=True)
		frappe.log_error(
			title=f"Credit release failed for job {job.name}",
			message=traceback.format_exc(),
		)


def _stop_for_cancellation(job):
	finalize_cancelled_job(
		job,
		cancelled_by=job.cancelled_by or job.user,
		cancel_reason=job.cancel_reason or "Job cancelled",
	)
	log_audit(
		"Cancel Job",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message="Job cancelled during processing.",
		user=job.user,
	)
	return True


def process_audio_separation(name: str):
	job = frappe.get_doc("Audio Separation Job", name)

	try:
		if should_stop_for_cancellation(job):
			_stop_for_cancellation(job)
			return

		job.status = "Uploading"
		job.started_at = job.started_at or now_datetime()
		job.save(ignore_permissions=True)
		frappe.db.commit()

		if should_stop_for_cancellation(job):
			_stop_for_cancellation(job)
			return

		local_audio_path = get_file_path(job.original_file)
		if not local_audio_path:
			frappe.throw(_("Could not resolve the attached audio file."))

		job.status = "Processing"
		job.save(ignore_permissions=True)
		frappe.db.commit()

		if should_stop_for_cancellation(job):
			_stop_for_cancellation(job)
			return

		result = isolate_vocal_and_instrumental(local_audio_path)

		if should_stop_for_cancellation(job):
			_release_job_credits_if_needed(
				job,
				reason=_("Cancellation completed after provider returned."),
			)
			finalize_cancelled_job(
				job,
				cancelled_by=job.cancelled_by or job.user,
				cancel_reason=job.cancel_reason
				or _("Cancellation completed after provider returned."),
			)
			log_audit(
				"Cancel Job",
				reference_doctype=job.doctype,
				reference_name=job.name,
				message=_("Cancellation completed after provider returned."),
				user=job.user,
			)
			return

		settings = get_settings()
		job.provider_cost_usd = calculate_provider_cost(job.duration_seconds, settings)
		had_downstream = job_had_downstream_assets(job)
		job.vocal_output_url = result.vocal_url
		job.instrumental_output_url = result.instrumental_url

		storage_warning = maybe_store_outputs_locally(job, result.vocal_url, result.instrumental_url)
		job.status = "Completed"
		job.cancellation_requested = 0
		job.completed_at = now_datetime()
		job.error_message = storage_warning
		job.save(ignore_permissions=True)
		frappe.db.commit()

		if had_downstream:
			job.reload()
			invalidate_downstream_assets(job)

		job.reload()
		_consume_job_credits_if_needed(job)
		from audio_stem.utils.notifications import notify_job_completed

		notify_job_completed(job)
		log_audit(
			"Complete Job",
			reference_doctype=job.doctype,
			reference_name=job.name,
			message="Job completed.",
			user=job.user,
		)

	except Exception as exc:
		frappe.db.rollback()
		job.reload()

		if should_stop_for_cancellation(job):
			_stop_for_cancellation(job)
			return

		job.status = "Failed"
		job.error_message = safe_error_message(exc)
		job.completed_at = now_datetime()
		job.save(ignore_permissions=True)
		frappe.db.commit()

		_release_job_credits_if_needed(job, reason=job.error_message)

		from audio_stem.utils.notifications import notify_job_failed

		notify_job_failed(job)
		log_audit(
			"Fail Job",
			reference_doctype=job.doctype,
			reference_name=job.name,
			message=job.error_message,
			user=job.user,
		)

		frappe.log_error(
			title=f"Audio separation failed for {job.name}",
			message=traceback.format_exc(),
		)
