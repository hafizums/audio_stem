# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os

import frappe
from frappe import _
from frappe.utils import cint, flt

ACTIVE_STATUSES = ("Queued", "Uploading", "Processing")
STARTABLE_STATUSES = ("Draft", "Failed")
BYTES_PER_MB = 1024 * 1024


def get_settings():
	return frappe.get_single("Audio Separation Settings")


def ensure_enabled(settings=None):
	settings = settings or get_settings()
	if not cint(settings.enabled):
		frappe.throw(_("Audio separation is disabled in Audio Separation Settings."))


def get_file_size_mb(file_doc) -> float:
	file_path = file_doc.get_full_path()
	if file_path and os.path.exists(file_path):
		return os.path.getsize(file_path) / BYTES_PER_MB

	if file_doc.file_size:
		return flt(file_doc.file_size) / BYTES_PER_MB

	return 0


def validate_file_size(file_doc, settings=None):
	settings = settings or get_settings()
	max_mb = cint(settings.max_file_size_mb)
	if not max_mb:
		return

	size_mb = get_file_size_mb(file_doc)
	if size_mb > max_mb:
		frappe.throw(
			_("File size ({0:.2f} MB) exceeds the maximum allowed size of {1} MB.").format(size_mb, max_mb)
		)


def validate_duration(duration_seconds, settings=None, *, require_duration=False):
	settings = settings or get_settings()
	max_seconds = cint(settings.max_audio_duration_seconds)

	if duration_seconds is None or cint(duration_seconds) <= 0:
		if require_duration:
			frappe.throw(
				_(
					"Audio duration could not be detected. Separation cannot be started until duration is available."
				)
			)
		return

	if max_seconds and cint(duration_seconds) > max_seconds:
		frappe.throw(
			_("Audio duration ({0}s) exceeds the maximum allowed duration of {1}s.").format(
				cint(duration_seconds), max_seconds
			)
		)


def calculate_provider_cost(duration_seconds, settings=None) -> float:
	settings = settings or get_settings()
	duration = cint(duration_seconds)
	if not duration:
		return 0
	return flt(duration) * flt(settings.cost_per_second_usd)


def user_has_other_active_job(user: str, exclude_job_name: str | None = None) -> bool:
	filters = {"user": user, "status": ("in", list(ACTIVE_STATUSES))}
	job_names = frappe.get_all("Audio Separation Job", filters=filters, pluck="name", ignore_permissions=True)
	if exclude_job_name:
		job_names = [name for name in job_names if name != exclude_job_name]
	return bool(job_names)


def ensure_single_active_job(user: str, exclude_job_name: str | None = None):
	if user_has_other_active_job(user, exclude_job_name=exclude_job_name):
		frappe.throw(_("You already have an active separation job. Please wait for it to finish."))


def get_limits_payload(settings=None):
	settings = settings or get_settings()
	return {
		"enabled": cint(settings.enabled),
		"max_file_size_mb": cint(settings.max_file_size_mb),
		"max_audio_duration_seconds": cint(settings.max_audio_duration_seconds),
		"cost_per_second_usd": flt(settings.cost_per_second_usd),
	}
