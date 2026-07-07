# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime, today

from audio_stem.utils.limits import calculate_provider_cost, get_settings

COUNTED_STATUSES = ("Queued", "Uploading", "Processing", "Completed")


def _is_system_manager(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _today_bounds():
	day = getdate(today())
	start = f"{day} 00:00:00"
	end = f"{day} 23:59:59.999999"
	return start, end


def get_user_daily_usage(user: str | None = None) -> dict:
	user = user or frappe.session.user
	start, end = _today_bounds()

	jobs = frappe.get_all(
		"Audio Separation Job",
		filters={
			"user": user,
			"creation": ("between", [start, end]),
		},
		fields=["name", "status", "duration_seconds", "provider_cost_usd", "credit_status"],
		ignore_permissions=True,
	)

	job_count = 0
	duration_seconds = 0
	cost_usd = 0.0
	settings = get_settings()

	for row in jobs:
		if row.status in COUNTED_STATUSES:
			job_count += 1
			duration_seconds += cint(row.duration_seconds)
			cost_usd += flt(row.provider_cost_usd)
		elif row.status == "Failed" and row.credit_status == "Consumed":
			job_count += 1
			duration_seconds += cint(row.duration_seconds)
			cost_usd += flt(row.provider_cost_usd)

	return {
		"jobs_today": job_count,
		"duration_seconds_today": duration_seconds,
		"cost_usd_today": cost_usd,
	}


def get_daily_limit_status(user: str | None = None, *, pending_job=None) -> dict:
	user = user or frappe.session.user
	settings = get_settings()
	usage = get_user_daily_usage(user)

	job_limit = cint(settings.daily_job_limit_per_user)
	duration_limit = cint(settings.daily_duration_limit_seconds_per_user)
	cost_limit = flt(settings.daily_cost_limit_usd_per_user)

	pending_duration = cint(pending_job.duration_seconds) if pending_job else 0
	pending_cost = (
		flt(pending_job.provider_cost_usd)
		if pending_job and pending_job.provider_cost_usd
		else calculate_provider_cost(pending_duration, settings)
	)

	def _remaining(limit, used, pending=0):
		if not limit:
			return None
		return max(limit - used - pending, 0)

	status = {
		"jobs_today": usage["jobs_today"],
		"duration_seconds_today": usage["duration_seconds_today"],
		"cost_usd_today": usage["cost_usd_today"],
		"daily_job_limit_per_user": job_limit,
		"daily_duration_limit_seconds_per_user": duration_limit,
		"daily_cost_limit_usd_per_user": cost_limit,
		"jobs_remaining": _remaining(job_limit, usage["jobs_today"], 1 if pending_job else 0),
		"duration_seconds_remaining": _remaining(
			duration_limit, usage["duration_seconds_today"], pending_duration
		),
		"cost_usd_remaining": _remaining(cost_limit, usage["cost_usd_today"], pending_cost),
		"limits_enabled": bool(job_limit or duration_limit or cost_limit),
	}
	return status


def ensure_daily_limits_for_queue(user: str | None = None, job=None):
	user = user or frappe.session.user
	if _is_system_manager(user):
		return

	settings = get_settings()
	usage = get_user_daily_usage(user)
	pending_duration = cint(job.duration_seconds) if job else 0
	pending_cost = calculate_provider_cost(pending_duration, settings)

	job_limit = cint(settings.daily_job_limit_per_user)
	if job_limit and usage["jobs_today"] + 1 > job_limit:
		frappe.throw(
			_("Daily job limit reached ({0} jobs per day).").format(job_limit),
			frappe.ValidationError,
		)

	duration_limit = cint(settings.daily_duration_limit_seconds_per_user)
	if duration_limit and usage["duration_seconds_today"] + pending_duration > duration_limit:
		frappe.throw(
			_("Daily audio duration limit reached ({0} seconds per day).").format(duration_limit),
			frappe.ValidationError,
		)

	cost_limit = flt(settings.daily_cost_limit_usd_per_user)
	if cost_limit and flt(usage["cost_usd_today"]) + pending_cost > cost_limit:
		frappe.throw(
			_("Daily provider cost limit reached ({0} USD per day).").format(cost_limit),
			frappe.ValidationError,
		)
