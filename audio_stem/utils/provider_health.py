# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe.utils import add_to_date, cint, flt, now_datetime


def get_provider_health_summary() -> dict:
	since = add_to_date(now_datetime(), hours=-24)
	jobs = frappe.get_all(
		"Audio Separation Job",
		filters={"modified": (">=", since)},
		fields=["name", "status", "started_at", "completed_at", "duration_seconds"],
		ignore_permissions=True,
	)

	completed = [row for row in jobs if row.status == "Completed"]
	failed = [row for row in jobs if row.status == "Failed"]
	terminal = completed + failed

	if not terminal:
		return {
			"status": "unknown",
			"success_rate": None,
			"completed_count": 0,
			"failed_count": 0,
			"average_processing_seconds": None,
			"last_success_at": None,
			"last_failure_at": None,
			"message": "No completed or failed jobs in the last 24 hours.",
		}

	success_rate = flt(len(completed)) / flt(len(terminal))
	durations = []
	for row in completed:
		if row.started_at and row.completed_at:
			delta = frappe.utils.get_datetime(row.completed_at) - frappe.utils.get_datetime(row.started_at)
			durations.append(max(delta.total_seconds(), 0))

	last_success = max((row.completed_at for row in completed if row.completed_at), default=None)
	last_failure = max((row.completed_at for row in failed if row.completed_at), default=None)

	status = "ok"
	message = "Provider outcomes look healthy."
	if success_rate < 0.5:
		status = "error"
		message = "High failure rate in the last 24 hours."
	elif success_rate < 0.8 or len(failed) >= 3:
		status = "warning"
		message = "Elevated failure rate in the last 24 hours."

	return {
		"status": status,
		"success_rate": round(success_rate, 3),
		"completed_count": len(completed),
		"failed_count": len(failed),
		"average_processing_seconds": round(sum(durations) / len(durations), 1) if durations else None,
		"last_success_at": last_success,
		"last_failure_at": last_failure,
		"message": message,
	}
