# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import traceback

import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe.utils.file_manager import get_file_path

from audio_stem.integrations.wavespeed_client import isolate_vocal_and_instrumental
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
		job.credit_status = "Failed"
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

	try:
		release_job_reservation(job, reason=reason)
		job.save(ignore_permissions=True)
	except Exception as exc:
		job.credit_error = safe_error_message(exc)
		job.save(ignore_permissions=True)
		frappe.log_error(
			title=f"Credit release failed for job {job.name}",
			message=traceback.format_exc(),
		)


def process_audio_separation(name: str):
	job = frappe.get_doc("Audio Separation Job", name)

	try:
		job.status = "Uploading"
		job.started_at = now_datetime()
		job.save(ignore_permissions=True)
		frappe.db.commit()

		local_audio_path = get_file_path(job.original_file)
		if not local_audio_path:
			frappe.throw(_("Could not resolve the attached audio file."))

		job.status = "Processing"
		job.save(ignore_permissions=True)
		frappe.db.commit()

		result = isolate_vocal_and_instrumental(local_audio_path)

		settings = get_settings()
		job.provider_cost_usd = calculate_provider_cost(job.duration_seconds, settings)
		job.vocal_output_url = result.vocal_url
		job.instrumental_output_url = result.instrumental_url

		storage_warning = maybe_store_outputs_locally(job, result.vocal_url, result.instrumental_url)
		job.status = "Completed"
		job.completed_at = now_datetime()
		job.error_message = storage_warning
		job.save(ignore_permissions=True)
		frappe.db.commit()

		job.reload()
		_consume_job_credits_if_needed(job)

	except Exception as exc:
		frappe.db.rollback()
		job.reload()
		job.status = "Failed"
		job.error_message = safe_error_message(exc)
		job.completed_at = now_datetime()
		job.save(ignore_permissions=True)
		frappe.db.commit()

		_release_job_credits_if_needed(job, reason=job.error_message)

		frappe.log_error(
			title=f"Audio separation failed for {job.name}",
			message=traceback.format_exc(),
		)
