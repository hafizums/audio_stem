# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.limits import get_settings

SOURCE_APP = "audio_stem"
DEFAULT_CREDIT_TYPE = "AUDIO_STEM"
DEFAULT_OWNER_DOCTYPE = "User"
REFERENCE_DOCTYPE = "Audio Separation Job"


def credit_management_available() -> bool:
	return bool(frappe.db.exists("DocType", "Credit Settings"))


def is_credit_management_enabled() -> bool:
	settings = get_settings()
	return bool(cint(settings.credit_management_enabled))


def credit_integration_ready() -> bool:
	return is_credit_management_enabled() and credit_management_available()


def _require_credit_api():
	if not credit_management_available():
		frappe.throw(
			_("Credit Management is enabled but the credit_management app is not installed."),
			frappe.ValidationError,
		)


def get_audio_credit_type() -> str:
	settings = get_settings()
	return settings.credit_type or DEFAULT_CREDIT_TYPE


def get_credit_owner_doctype() -> str:
	settings = get_settings()
	return settings.credit_owner_doctype or DEFAULT_OWNER_DOCTYPE


def _idempotency_key(job_name: str, action: str) -> str:
	return f"audio_stem:{job_name}:{action}"


def get_user_credit_balance(user: str, credit_type: str | None = None) -> dict:
	_require_credit_api()
	import credit_management.api as credit_api

	credit_type = credit_type or get_audio_credit_type()
	return credit_api.get_balance(get_credit_owner_doctype(), user, credit_type)


def reserve_job_credits(job) -> dict:
	_require_credit_api()
	import credit_management.api as credit_api

	amount = flt(job.provider_cost_usd)
	if amount <= 0:
		frappe.throw(_("Provider cost must be greater than zero to reserve credits."))

	credit_type = job.credit_type or get_audio_credit_type()
	balance = credit_api.get_balance(get_credit_owner_doctype(), job.user, credit_type)
	if flt(balance.get("available_balance")) < amount:
		frappe.throw(_("Insufficient available credits for this separation job."))

	if job.credit_reservation and job.credit_status == "Reserved":
		return {
			"reservation": job.credit_reservation,
			"reserved_amount": flt(job.reserved_amount),
			"idempotent_replay": True,
		}

	result = credit_api.reserve_credits(
		owner_doctype=get_credit_owner_doctype(),
		owner_name=job.user,
		credit_type=credit_type,
		amount=amount,
		reference_doctype=REFERENCE_DOCTYPE,
		reference_name=job.name,
		idempotency_key=_idempotency_key(job.name, "reserve"),
		source_app=SOURCE_APP,
		metadata={
			"duration_seconds": job.duration_seconds,
			"original_filename": job.original_filename,
		},
	)

	job.credit_reservation = result.get("reservation")
	job.reserved_amount = flt(result.get("reserved_amount", amount))
	job.credit_type = credit_type
	job.credit_status = "Reserved"
	job.credit_error = None
	job.consumed_amount = 0

	return result


def consume_job_reservation(job) -> dict:
	_require_credit_api()
	import credit_management.api as credit_api

	if not job.credit_reservation:
		frappe.throw(_("No credit reservation found for this job."))

	actual_amount = flt(job.provider_cost_usd)
	result = credit_api.consume_reserved_credits(
		reservation_name=job.credit_reservation,
		actual_amount=actual_amount,
		idempotency_key=_idempotency_key(job.name, "consume"),
		source_app=SOURCE_APP,
		metadata={"job": job.name},
	)

	job.credit_status = "Consumed"
	job.consumed_amount = flt(result.get("consumed_amount", actual_amount))
	job.credit_error = None
	return result


def release_job_reservation(job, reason: str | None = None) -> dict:
	_require_credit_api()
	import credit_management.api as credit_api

	if not job.credit_reservation:
		return {"status": "skipped"}

	result = credit_api.release_reservation(
		reservation_name=job.credit_reservation,
		reason=reason,
		idempotency_key=_idempotency_key(job.name, "release"),
	)

	job.credit_status = "Released"
	job.credit_error = None
	return result
