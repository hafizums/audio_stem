# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import traceback

import frappe
from frappe.utils import flt, now_datetime
from frappe.utils.file_manager import get_file_path

from audio_stem.integrations.wavespeed_client import isolate_vocal_and_instrumental


def process_audio_separation(job_name: str):
	job = frappe.get_doc("Audio Separation Job", job_name)

	try:
		job.status = "Uploading"
		job.started_at = now_datetime()
		job.save(ignore_permissions=True)
		frappe.db.commit()

		local_audio_path = get_file_path(job.original_file)
		if not local_audio_path:
			frappe.throw("Could not resolve the attached audio file.")

		job.status = "Processing"
		job.save(ignore_permissions=True)
		frappe.db.commit()

		result = isolate_vocal_and_instrumental(local_audio_path)

		settings = frappe.get_single("Audio Separation Settings")
		if job.duration_seconds:
			job.provider_cost_usd = flt(job.duration_seconds) * flt(settings.cost_per_second_usd)

		job.vocal_output_url = result.vocal_url
		job.instrumental_output_url = result.instrumental_url
		job.status = "Completed"
		job.completed_at = now_datetime()
		job.error_message = None
		job.save(ignore_permissions=True)
		frappe.db.commit()

	except Exception as exc:
		frappe.db.rollback()
		job.reload()
		job.status = "Failed"
		job.error_message = str(exc)[:500]
		job.completed_at = now_datetime()
		job.save(ignore_permissions=True)
		frappe.db.commit()

		frappe.log_error(
			title=f"Audio separation failed for {job.name}",
			message=traceback.format_exc(),
		)
