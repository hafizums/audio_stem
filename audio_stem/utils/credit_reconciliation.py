# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Credit reconciliation helpers for jobs where consume/release did not complete."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

CREDIT_RECONCILIATION_STATUS = "Reconciliation Required"
TERMINAL_SEPARATION_STATUSES = ("Completed", "Failed", "Cancelled")


def is_credit_reconciliation_needed(job) -> bool:
	if not job.credit_reservation:
		return False
	if job.credit_status == CREDIT_RECONCILIATION_STATUS:
		return True
	if job.status == "Completed" and job.credit_status == "Reserved":
		return True
	return False


def get_credit_reconciliation_issues(*, limit: int = 100) -> list[dict]:
	"""Return jobs that may have stuck credit reservations after separation."""
	limit = max(1, min(int(limit or 100), 500))
	filters = [
		["credit_reservation", "is", "set"],
		["status", "in", list(TERMINAL_SEPARATION_STATUSES)],
		["credit_status", "in", [CREDIT_RECONCILIATION_STATUS, "Failed", "Reserved"]],
	]
	rows = frappe.get_all(
		"Audio Separation Job",
		filters=filters,
		fields=[
			"name",
			"user",
			"status",
			"credit_status",
			"credit_reservation",
			"reserved_amount",
			"consumed_amount",
			"credit_error",
			"modified",
		],
		order_by="modified desc",
		limit=limit,
	)
	return [row for row in rows if is_credit_reconciliation_needed(frappe._dict(row)) or _failed_with_open_reservation(row)]


def _failed_with_open_reservation(row: dict) -> bool:
	return (
		row.get("credit_status") == "Failed"
		and row.get("credit_reservation")
		and row.get("status") in TERMINAL_SEPARATION_STATUSES
		and flt(row.get("reserved_amount")) > 0
		and flt(row.get("consumed_amount")) <= 0
	)


def retry_job_credit_consume(job) -> dict:
	"""Retry consuming a reserved credit after a completed separation job."""
	from audio_stem.integrations.credit_management_client import (
		consume_job_reservation,
		is_credit_management_enabled,
	)

	if not is_credit_management_enabled():
		frappe.throw(_("Credit management is not enabled."), frappe.ValidationError)

	if job.status != "Completed":
		frappe.throw(_("Credit reconciliation retry is only available for completed jobs."), frappe.ValidationError)

	if not job.credit_reservation:
		frappe.throw(_("No credit reservation found for this job."), frappe.ValidationError)

	if job.credit_status == "Consumed":
		return {
			"status": "Consumed",
			"consumed_amount": flt(job.consumed_amount),
			"idempotent_replay": True,
		}

	if job.credit_status not in (CREDIT_RECONCILIATION_STATUS, "Failed", "Reserved"):
		frappe.throw(_("This job does not require credit consumption reconciliation."), frappe.ValidationError)

	result = consume_job_reservation(job)
	job.credit_status = "Consumed"
	job.credit_error = None
	job.save(ignore_permissions=True)
	return result
