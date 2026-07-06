# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _

PROVIDER = "WaveSpeed"
PROVIDER_MODEL = "wavespeed-ai/audio-vocal-isolator"


@frappe.whitelist()
def start_separation(job_name: str):
	job = frappe.get_doc("Audio Separation Job", job_name)
	job.check_permission("write")

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
		job_name=f"audio_separation:{job.name}",
		job_id=f"audio_separation:{job.name}",
		job=job.name,
	)

	return {"status": job.status, "name": job.name}
