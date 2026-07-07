# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _

from audio_stem.utils.usage import get_usage_summary_data


def execute(filters=None):
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required"), frappe.PermissionError)
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	summary = get_usage_summary_data()
	columns = get_columns()
	data = get_data(summary)
	return columns, data


def get_columns():
	return [
		{"label": _("Metric"), "fieldname": "metric", "fieldtype": "Data", "width": 220},
		{"label": _("Value"), "fieldname": "value", "fieldtype": "Data", "width": 220},
	]


def get_data(summary):
	rows = [
		{"metric": _("Total Jobs"), "value": summary["total_jobs"]},
		{"metric": _("Completed Jobs"), "value": summary["completed_jobs"]},
		{"metric": _("Failed Jobs"), "value": summary["failed_jobs"]},
		{"metric": _("Queued / Processing Jobs"), "value": summary["active_jobs"]},
		{"metric": _("Total Duration Processed (seconds)"), "value": summary["total_duration_seconds"]},
		{"metric": _("Total Provider Cost (USD)"), "value": summary["total_provider_cost_usd"]},
		{"metric": "", "value": ""},
		{"metric": _("Jobs By User"), "value": _("User / Count")},
	]

	for row in summary["jobs_by_user"]:
		rows.append({"metric": row.user, "value": row.job_count})

	rows.extend(
		[
			{"metric": "", "value": ""},
			{"metric": _("Recent Failures"), "value": _("Job / User / Error")},
		]
	)

	for failure in summary["recent_failures"]:
		label = failure.name
		if failure.original_filename:
			label = f"{failure.name} ({failure.original_filename})"
		rows.append(
			{
				"metric": label,
				"value": f"{failure.user}: {failure.error_message or ''}",
			}
		)

	return rows
