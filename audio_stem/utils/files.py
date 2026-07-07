# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os

import frappe
from frappe.utils.file_manager import get_file_path


def is_external_file_url(file_url: str | None) -> bool:
	return bool(file_url and file_url.startswith(("http://", "https://")))


def resolve_frappe_file_path(file_url: str | None) -> str | None:
	"""Resolve a Frappe File URL to a local path. Never raises for external URLs."""
	if not file_url or is_external_file_url(file_url):
		return None

	file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if file_name:
		file_doc = frappe.get_doc("File", file_name)
		path = file_doc.get_full_path()
		if path and os.path.exists(path):
			return path

	try:
		path = get_file_path(file_url)
	except Exception:
		return None

	if path and os.path.exists(path):
		return path

	return None
