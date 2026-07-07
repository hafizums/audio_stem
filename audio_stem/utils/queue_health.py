# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from audio_stem.utils.limits import ACTIVE_STATUSES, get_settings


def get_queue_health_data() -> dict:
	settings = get_settings()
	threshold_minutes = cint(settings.stuck_job_threshold_minutes) or 30
	threshold_time = add_to_date(now_datetime(), minutes=-threshold_minutes)

	active_jobs = frappe.get_all(
		"Audio Separation Job",
		filters={"status": ("in", list(ACTIVE_STATUSES))},
		fields=["name", "user", "status", "creation", "started_at", "modified"],
		order_by="creation asc",
		ignore_permissions=True,
	)

	queued = sum(1 for row in active_jobs if row.status == "Queued")
	uploading = sum(1 for row in active_jobs if row.status == "Uploading")
	processing = sum(1 for row in active_jobs if row.status == "Processing")

	oldest_age_minutes = None
	stuck_jobs = []
	now = now_datetime()
	for row in active_jobs:
		anchor = row.started_at or row.creation
		if not anchor:
			continue
		age_minutes = (now - frappe.utils.get_datetime(anchor)).total_seconds() / 60
		if oldest_age_minutes is None or age_minutes > oldest_age_minutes:
			oldest_age_minutes = age_minutes
		anchor_dt = frappe.utils.get_datetime(anchor)
		if anchor_dt <= threshold_time:
			stuck_jobs.append(
				{
					"name": row.name,
					"user": row.user,
					"status": row.status,
					"age_minutes": round(age_minutes, 1),
					"creation": row.creation,
				}
			)

	recent_failures = frappe.db.count(
		"Audio Separation Job",
		{
			"status": "Failed",
			"modified": (">=", add_to_date(now_datetime(), hours=-24)),
		},
	)

	guidance = _("Queue looks healthy.")
	if stuck_jobs:
		guidance = _(
			"Some jobs appear stuck. Confirm the long-queue worker is running and review stuck jobs."
		)
	elif active_jobs and queued == len(active_jobs):
		guidance = _("Jobs are queued but not processing. Check the background worker.")

	return {
		"active_jobs_count": len(active_jobs),
		"queued_jobs_count": queued,
		"uploading_jobs_count": uploading,
		"processing_jobs_count": processing,
		"oldest_active_job_age_minutes": round(oldest_age_minutes, 1) if oldest_age_minutes is not None else None,
		"stuck_job_threshold_minutes": threshold_minutes,
		"stuck_jobs": stuck_jobs,
		"recent_failures_count": cint(recent_failures),
		"worker_guidance_message": guidance,
	}
