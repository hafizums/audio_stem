# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Dangerous one-off: delete ALL Audio Separation Jobs on a site."""

from __future__ import annotations

import frappe


def delete_all_audio_separation_jobs() -> dict:
	frappe.set_user("Administrator")
	job_count_before = frappe.db.count("Audio Separation Job")
	audit_before = frappe.db.count("Audio Stem Audit Log")
	file_names = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Audio Separation Job"},
		pluck="name",
	)

	frappe.db.delete(
		"Audio Stem Audit Log",
		{"reference_doctype": "Audio Separation Job"},
	)
	frappe.db.sql("DELETE FROM `tabAudio Stem Audit Log`")

	files_removed = 0
	for name in file_names:
		if frappe.db.exists("File", name):
			frappe.delete_doc("File", name, force=True, ignore_permissions=True)
			files_removed += 1

	frappe.db.sql("DELETE FROM `tabAudio Separation Job`")
	frappe.db.commit()

	return {
		"jobs_removed": job_count_before,
		"audit_logs_removed": audit_before,
		"attached_files_removed": files_removed,
		"jobs_remaining": frappe.db.count("Audio Separation Job"),
	}
