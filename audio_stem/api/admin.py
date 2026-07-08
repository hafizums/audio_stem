# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe

from audio_stem.utils.audit_log import log_audit
from audio_stem.utils.config_checklist import get_configuration_checklist_data
from audio_stem.utils.credit_reconciliation import (
	get_credit_reconciliation_issues as get_credit_reconciliation_issues_data,
	retry_job_credit_consume,
)
from audio_stem.utils.provider_health import get_provider_health_summary
from audio_stem.utils.queue_health import get_queue_health_data
from audio_stem.utils.usage import get_usage_summary, _require_system_manager


@frappe.whitelist()
def get_audio_stem_usage_summary():
	_require_system_manager()
	log_audit("Admin View", message="Viewed usage summary.")
	return get_usage_summary()


@frappe.whitelist()
def get_configuration_checklist():
	_require_system_manager()
	log_audit("Admin View", message="Viewed configuration checklist.")
	return get_configuration_checklist_data()


@frappe.whitelist()
def get_queue_health():
	_require_system_manager()
	log_audit("Admin View", message="Viewed queue health.")
	return get_queue_health_data()


@frappe.whitelist()
def get_provider_health():
	_require_system_manager()
	return get_provider_health_summary()


@frappe.whitelist()
def get_credit_reconciliation_issues(limit: int = 100):
	_require_system_manager()
	log_audit("Admin View", message="Viewed credit reconciliation issues.")
	return get_credit_reconciliation_issues_data(limit=limit)


@frappe.whitelist()
def retry_credit_reconciliation(job_name: str):
	_require_system_manager()
	job = frappe.get_doc("Audio Separation Job", job_name)
	result = retry_job_credit_consume(job)
	log_audit(
		"Retry Credit Reconciliation",
		reference_doctype=job.doctype,
		reference_name=job.name,
		message="Credit consume reconciliation retried.",
	)
	return {
		"job_name": job.name,
		"credit_status": job.credit_status,
		"consumed_amount": job.consumed_amount,
		"result": result,
	}
