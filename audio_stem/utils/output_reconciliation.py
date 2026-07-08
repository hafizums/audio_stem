# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Repair job output fields when attached files exist but links were lost."""


def sync_karaoke_output_fields_from_files(job) -> bool:
	"""Restore karaoke output URLs from the newest attached File rows."""
	changed = False
	field_patterns = {
		"karaoke_video_file": f"{job.name}-karaoke.mp4",
		"karaoke_ass_file": f"{job.name}-karaoke.ass",
		"karaoke_render_source_video_file": f"{job.name}-karaoke-source.mp4",
	}

	for fieldname, file_name in field_patterns.items():
		if getattr(job, fieldname, None):
			continue
		file_url = _latest_attached_file_url(job, file_name=file_name, fieldname=fieldname)
		if file_url:
			job.set(fieldname, file_url)
			changed = True

	if changed:
		job.save(ignore_permissions=True)
	return changed


def _latest_attached_file_url(job, *, file_name: str, fieldname: str) -> str | None:
	import frappe

	rows = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": job.doctype,
			"attached_to_name": job.name,
			"file_name": file_name,
		},
		fields=["file_url"],
		order_by="creation desc",
		limit=1,
	)
	if rows:
		return rows[0].file_url

	rows = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": job.doctype,
			"attached_to_name": job.name,
			"attached_to_field": fieldname,
		},
		fields=["file_url"],
		order_by="creation desc",
		limit=1,
	)
	return rows[0].file_url if rows else None
