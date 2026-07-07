# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import traceback

import frappe
from frappe import _
from frappe.utils import cint, get_url

from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.limits import get_settings

VOCAL_REMOVER_PATH = "/audio-vocal-remover"


def notify_job_completed(job):
	settings = get_settings()
	if not cint(settings.notify_user_on_completion):
		return

	subject = _("Audio separation completed: {0}").format(job.name)
	message = _(
		"Your audio separation job {0} has completed successfully."
		"\n\nOpen the vocal remover: {1}"
	).format(job.name, get_url(VOCAL_REMOVER_PATH))

	_send_job_notification(job, subject, message)


def notify_job_failed(job):
	settings = get_settings()
	if not cint(settings.notify_user_on_failure):
		return

	safe_error = safe_error_message(Exception(job.error_message)) if job.error_message else _("Separation failed.")
	subject = _("Audio separation failed: {0}").format(job.name)
	message = _(
		"Your audio separation job {0} failed."
		"\n\nReason: {1}"
		"\n\nOpen the vocal remover: {2}"
	).format(job.name, safe_error, get_url(VOCAL_REMOVER_PATH))

	_send_job_notification(job, subject, message)


def _send_job_notification(job, subject: str, message: str):
	try:
		_create_notification_log(job, subject, message)
		if frappe.db.get_value("User", job.user, "email"):
			frappe.sendmail(
				recipients=[job.user],
				subject=subject,
				message=message,
				now=True,
			)
	except Exception:
		frappe.log_error(
			title=f"Audio separation notification failed for {job.name}",
			message=traceback.format_exc(),
		)


def _create_notification_log(job, subject: str, message: str):
	notification = frappe.new_doc("Notification Log")
	notification.for_user = job.user
	notification.type = "Alert"
	notification.document_type = job.doctype
	notification.document_name = job.name
	notification.subject = subject
	notification.email_content = message
	notification.insert(ignore_permissions=True)
