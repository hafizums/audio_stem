# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from audio_stem.api.admin import get_configuration_checklist
from audio_stem.api.separation import get_job_detail, get_page_settings
from audio_stem.integrations.wavespeed_client import SeparationResult
from audio_stem.utils.config_checklist import get_configuration_checklist_data
from audio_stem.utils.notifications import notify_job_completed, notify_job_failed
from audio_stem.workers.separation_worker import process_audio_separation

APP_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_CREDIT_PATTERNS = (
	"Credit Account",
	"Credit Ledger Entry",
	"Credit Ledger",
	"tabCredit Account",
	"tabCredit Ledger Entry",
)


class TestAudioSeparationMilestone6(FrappeTestCase):
	def setUp(self):
		settings = frappe.get_single("Audio Separation Settings")
		self._saved = {
			"enabled": settings.enabled,
			"wavespeed_api_key": settings.get_password("wavespeed_api_key", raise_exception=False),
			"max_file_size_mb": settings.max_file_size_mb,
			"max_audio_duration_seconds": settings.max_audio_duration_seconds,
			"cost_per_second_usd": settings.cost_per_second_usd,
			"store_outputs_locally": settings.store_outputs_locally,
			"credit_management_enabled": settings.credit_management_enabled,
			"credit_type": settings.credit_type,
			"credit_owner_doctype": settings.credit_owner_doctype,
			"cleanup_enabled": getattr(settings, "cleanup_enabled", 0),
			"retention_days": getattr(settings, "retention_days", 7),
			"delete_original_after_completion": getattr(settings, "delete_original_after_completion", 0),
			"delete_outputs_after_retention": getattr(settings, "delete_outputs_after_retention", 0),
			"notify_user_on_completion": getattr(settings, "notify_user_on_completion", 0),
			"notify_user_on_failure": getattr(settings, "notify_user_on_failure", 0),
		}
		settings.enabled = 1
		settings.wavespeed_api_key = "test-api-key"
		settings.max_file_size_mb = 50
		settings.max_audio_duration_seconds = 600
		settings.cost_per_second_usd = 0.001
		settings.store_outputs_locally = 0
		settings.credit_management_enabled = 0
		settings.credit_type = "AUDIO_STEM"
		settings.credit_owner_doctype = "User"
		settings.cleanup_enabled = 0
		settings.retention_days = 7
		settings.delete_original_after_completion = 0
		settings.delete_outputs_after_retention = 0
		settings.notify_user_on_completion = 0
		settings.notify_user_on_failure = 0
		settings.save(ignore_permissions=True)
		frappe.set_user("Administrator")

	def tearDown(self):
		settings = frappe.get_single("Audio Separation Settings")
		for field, value in self._saved.items():
			if field == "wavespeed_api_key":
				settings.wavespeed_api_key = value or ""
			else:
				setattr(settings, field, value)
		settings.save(ignore_permissions=True)
		frappe.set_user("Administrator")

	def _create_file(self):
		with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
			tmp.write(b"audio")
			tmp_path = tmp.name

		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": os.path.basename(tmp_path),
				"is_private": 1,
				"content": open(tmp_path, "rb").read(),
			}
		)
		file_doc.save(ignore_permissions=True)
		os.unlink(tmp_path)
		return file_doc

	def _create_job(self, user=None, **kwargs):
		job = frappe.get_doc(
			{
				"doctype": "Audio Separation Job",
				"user": user or frappe.session.user,
				"status": "Completed",
				"original_file": self._create_file().file_url,
				"duration_seconds": 30,
				**kwargs,
			}
		)
		job.insert(ignore_permissions=True)
		return job

	def test_owner_can_read_job_detail_payload(self):
		job = self._create_job(
			original_filename="song.mp3",
			vocal_output_url="https://example.com/vocal.mp3",
			instrumental_output_url="https://example.com/instrumental.mp3",
			started_at=frappe.utils.now_datetime(),
			completed_at=frappe.utils.now_datetime(),
		)
		detail = get_job_detail(job.name)
		self.assertEqual(detail["name"], job.name)
		self.assertEqual(detail["original_filename"], "song.mp3")
		self.assertIn("started_at", detail)
		self.assertIn("cleanup_notes", detail)

	def test_another_user_cannot_read_job_detail_payload(self):
		owner = "detail-owner@example.com"
		other = "detail-other@example.com"
		for email, name in ((owner, "Owner"), (other, "Other")):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": name,
						"send_welcome_email": 0,
					}
				).insert(ignore_permissions=True)

		job = self._create_job(user=owner)
		frappe.set_user(other)
		with self.assertRaises(frappe.PermissionError):
			get_job_detail(job.name)
		frappe.set_user("Administrator")

	def test_system_manager_can_read_job_detail_payload(self):
		owner = "detail-sm-owner@example.com"
		if not frappe.db.exists("User", owner):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": owner,
					"first_name": "SMOwner",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)

		job = self._create_job(user=owner)
		frappe.set_user("Administrator")
		detail = get_job_detail(job.name)
		self.assertEqual(detail["name"], job.name)

	def test_job_detail_does_not_expose_tracebacks_or_secrets(self):
		job = self._create_job(
			error_message="Audio processing failed. Please try again or contact an administrator.",
			credit_error="Unable to verify credit balance.",
		)
		detail = get_job_detail(job.name)
		payload = frappe.as_json(detail)
		self.assertNotIn("Traceback", payload)
		self.assertNotIn("WAVESPEED_API_KEY", payload)
		self.assertNotIn("test-api-key", payload)

	def test_completed_notification_respects_setting(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.notify_user_on_completion = 0
		settings.save(ignore_permissions=True)
		job = self._create_job()

		with patch("audio_stem.utils.notifications._send_job_notification") as send_mock:
			notify_job_completed(job)
		send_mock.assert_not_called()

		settings.notify_user_on_completion = 1
		settings.save(ignore_permissions=True)
		with patch("audio_stem.utils.notifications._send_job_notification") as send_mock:
			notify_job_completed(job)
		send_mock.assert_called_once()

	def test_failed_notification_respects_setting(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.notify_user_on_failure = 0
		settings.save(ignore_permissions=True)
		job = self._create_job(status="Failed", error_message="Safe failure")

		with patch("audio_stem.utils.notifications._send_job_notification") as send_mock:
			notify_job_failed(job)
		send_mock.assert_not_called()

		settings.notify_user_on_failure = 1
		settings.save(ignore_permissions=True)
		with patch("audio_stem.utils.notifications._send_job_notification") as send_mock:
			notify_job_failed(job)
		send_mock.assert_called_once()

	def test_notification_failure_does_not_fail_job(self):
		job = self._create_job(status="Queued")
		job.db_set("status", "Queued")

		with patch(
			"audio_stem.workers.separation_worker.isolate_vocal_and_instrumental",
			return_value=SeparationResult(
				vocal_url="https://example.com/vocal.mp3",
				instrumental_url="https://example.com/instrumental.mp3",
			),
		), patch(
			"audio_stem.workers.separation_worker.get_file_path",
			return_value=self._create_file().get_full_path(),
		), patch(
			"audio_stem.utils.notifications._create_notification_log",
			side_effect=RuntimeError("notification failed"),
		):
			process_audio_separation(job.name)

		job.reload()
		self.assertEqual(job.status, "Completed")

	def test_notification_message_does_not_include_traceback_or_api_key(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.notify_user_on_failure = 1
		settings.save(ignore_permissions=True)

		job = self._create_job(
			status="Failed",
			error_message="Traceback (most recent call last): secret WAVESPEED_API_KEY=abc",
		)

		with patch("audio_stem.utils.notifications.frappe.sendmail") as sendmail_mock, patch(
			"audio_stem.utils.notifications._create_notification_log"
		):
			notify_job_failed(job)

		self.assertTrue(sendmail_mock.called)
		message = sendmail_mock.call_args.kwargs.get("message") or sendmail_mock.call_args[1].get("message")
		self.assertIn(job.name, message)
		self.assertIn("/audio-vocal-remover", message)
		self.assertNotIn("WAVESPEED_API_KEY", message)

	def test_system_manager_can_access_configuration_checklist(self):
		items = get_configuration_checklist()
		keys = {item["key"] for item in items}
		self.assertIn("wavespeed_api_key", keys)
		self.assertIn("scheduler_hook", keys)

	def test_normal_user_cannot_access_configuration_checklist(self):
		user = "checklist-user@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user,
					"first_name": "Checklist",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)

		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			get_configuration_checklist()
		frappe.set_user("Administrator")

	def test_checklist_does_not_expose_api_key(self):
		items = get_configuration_checklist()
		payload = frappe.as_json(items)
		self.assertNotIn("test-api-key", payload)

	def test_checklist_reports_missing_api_key_safely(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.wavespeed_api_key = ""
		settings.save(ignore_permissions=True)

		items = get_configuration_checklist_data()
		api_item = next(item for item in items if item["key"] == "wavespeed_api_key")
		self.assertEqual(api_item["status"], "error")
		self.assertIn("missing", api_item["message"].lower())

	def test_checklist_reports_credit_management_unavailable_when_enabled(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.credit_management_enabled = 1
		settings.save(ignore_permissions=True)

		with patch(
			"audio_stem.integrations.credit_management_client.credit_management_available",
			return_value=False,
		):
			items = get_configuration_checklist_data()

		credit_item = next(item for item in items if item["key"] == "credit_integration")
		self.assertEqual(credit_item["status"], "error")

	def test_page_settings_include_onboarding_fields(self):
		settings = get_page_settings()
		self.assertIn("accepted_file_types", settings)
		self.assertIn("is_system_manager", settings)
		self.assertNotIn("wavespeed_api_key", settings)

	def test_audio_stem_runtime_does_not_reference_credit_ledger_doctypes(self):
		runtime_roots = [
			APP_ROOT / "api",
			APP_ROOT / "integrations",
			APP_ROOT / "workers",
			APP_ROOT / "utils",
		]
		violations = []
		for root in runtime_roots:
			for path in root.rglob("*.py"):
				content = path.read_text(encoding="utf-8")
				for pattern in FORBIDDEN_CREDIT_PATTERNS:
					if pattern in content:
						violations.append(f"{path}: {pattern}")
		self.assertEqual(violations, [])

	def test_worker_failure_still_notifies_when_enabled(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.notify_user_on_failure = 1
		settings.save(ignore_permissions=True)

		job = self._create_job(status="Queued")
		job.db_set("status", "Queued")

		with patch(
			"audio_stem.workers.separation_worker.isolate_vocal_and_instrumental",
			side_effect=RuntimeError("provider exploded"),
		), patch(
			"audio_stem.workers.separation_worker.get_file_path",
			return_value=self._create_file().get_full_path(),
		), patch("audio_stem.utils.notifications.notify_job_failed") as notify_mock:
			process_audio_separation(job.name)

		notify_mock.assert_called_once()
		job.reload()
		self.assertEqual(job.status, "Failed")
