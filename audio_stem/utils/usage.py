# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe.utils import cint, flt

from audio_stem.utils.limits import ACTIVE_STATUSES


def _require_system_manager():
	if frappe.session.user == "Guest":
		frappe.throw("Login required", frappe.PermissionError)
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		frappe.throw("Not permitted", frappe.PermissionError)


def get_usage_summary_data():
	total_jobs = frappe.db.count("Audio Separation Job")
	completed_jobs = frappe.db.count("Audio Separation Job", {"status": "Completed"})
	failed_jobs = frappe.db.count("Audio Separation Job", {"status": "Failed"})
	active_jobs = frappe.db.count("Audio Separation Job", {"status": ("in", list(ACTIVE_STATUSES))})

	totals = frappe.db.sql(
		"""
		SELECT
			IFNULL(SUM(duration_seconds), 0) AS total_duration_seconds,
			IFNULL(SUM(provider_cost_usd), 0) AS total_provider_cost_usd
		FROM `tabAudio Separation Job`
		WHERE status = 'Completed'
		""",
		as_dict=True,
	)[0]

	jobs_by_user = frappe.db.sql(
		"""
		SELECT user, COUNT(*) AS job_count
		FROM `tabAudio Separation Job`
		GROUP BY user
		ORDER BY job_count DESC, user ASC
		""",
		as_dict=True,
	)

	recent_failures = frappe.get_all(
		"Audio Separation Job",
		filters={"status": "Failed"},
		fields=["name", "user", "original_filename", "error_message", "completed_at", "creation"],
		order_by="completed_at desc, creation desc",
		limit=20,
	)

	return {
		"total_jobs": cint(total_jobs),
		"completed_jobs": cint(completed_jobs),
		"failed_jobs": cint(failed_jobs),
		"active_jobs": cint(active_jobs),
		"total_duration_seconds": cint(totals.total_duration_seconds),
		"total_provider_cost_usd": flt(totals.total_provider_cost_usd),
		"jobs_by_user": jobs_by_user,
		"recent_failures": recent_failures,
	}


def get_usage_summary():
	_require_system_manager()
	return get_usage_summary_data()
