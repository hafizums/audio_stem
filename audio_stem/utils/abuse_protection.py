# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, getdate, now_datetime, today

from audio_stem.utils.limits import get_settings


def _is_system_manager(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def ensure_create_allowed(user: str | None = None):
	user = user or frappe.session.user
	if _is_system_manager(user):
		return

	settings = get_settings()
	limit = cint(settings.hourly_create_limit_per_user)
	if not limit:
		return

	since = add_to_date(now_datetime(), hours=-1)
	count = frappe.db.count(
		"Audio Separation Job",
		{"user": user, "creation": (">=", since)},
	)

	if count >= limit:
		frappe.throw(
			_("Too many job creation attempts. Please wait before creating another job."),
			frappe.ValidationError,
		)


def ensure_start_allowed(user: str | None = None):
	user = user or frappe.session.user
	if _is_system_manager(user):
		return

	settings = get_settings()
	limit = cint(settings.daily_failed_job_limit_per_user)
	if not limit:
		return

	day = getdate(today())
	start = f"{day} 00:00:00"
	end = f"{day} 23:59:59.999999"

	failed_count = frappe.db.count(
		"Audio Separation Job",
		{
			"user": user,
			"status": "Failed",
			"creation": ("between", [start, end]),
		},
	)

	if failed_count >= limit:
		frappe.throw(
			_("Too many failed jobs today. New starts are temporarily blocked."),
			frappe.ValidationError,
		)
