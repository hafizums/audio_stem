# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json

import frappe
from frappe.utils import now_datetime

SENSITIVE_MARKERS = ("api_key", "wavespeed", "traceback", "bearer ")


def log_audit(
	action: str,
	*,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	message: str | None = None,
	metadata: dict | None = None,
	user: str | None = None,
):
	user = user or frappe.session.user
	safe_message = _sanitize_text(message)
	safe_metadata = _sanitize_metadata(metadata)

	request = getattr(frappe.local, "request", None)
	ip_address = getattr(request, "remote_addr", None) if request else None
	user_agent = None
	if request and getattr(request, "headers", None):
		user_agent = request.headers.get("User-Agent")

	doc = frappe.get_doc(
		{
			"doctype": "Audio Stem Audit Log",
			"user": user,
			"action": action,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"ip_address": ip_address,
			"user_agent": (user_agent or "")[:140],
			"message": safe_message,
			"metadata_json": json.dumps(safe_metadata) if safe_metadata else None,
			"created_at": now_datetime(),
		}
	)
	doc.insert(ignore_permissions=True)


def _sanitize_text(value: str | None) -> str | None:
	if not value:
		return value
	lower = value.lower()
	for marker in SENSITIVE_MARKERS:
		if marker in lower:
			return "Action recorded."
	return value[:500]


def _sanitize_metadata(metadata: dict | None) -> dict | None:
	if not metadata:
		return None

	safe = {}
	for key, value in metadata.items():
		text = str(value)
		lower = text.lower()
		if any(marker in lower for marker in SENSITIVE_MARKERS):
			continue
		if len(text) > 500:
			text = text[:497] + "..."
		safe[key] = text
	return safe
