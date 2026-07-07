# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from audio_stem.api.admin import get_audio_stem_usage_summary
from audio_stem.api.separation import (
	create_job_zip,
	get_recent_jobs,
	retry_failed_job,
	start_separation,
)
from audio_stem.audio_stem.report.audio_stem_usage_summary.audio_stem_usage_summary import execute
from audio_stem.utils.cleanup import cleanup_old_audio_jobs

CLIENT = "audio_stem.integrations.credit_management_client"
APP_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_CREDIT_PATTERNS = (
	"Credit Account",
	"Credit Ledger Entry",
	"Credit Ledger",
	"tabCredit Account",
	"tabCredit Ledger Entry",
)


class TestAudioSeparationMilestone5(FrappeTestCase):
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

	def _create_file(self, content=b"audio"):
		with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
			tmp.write(content)
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

	def _create_job(self, status="Draft", duration_seconds=30, user=None, **kwargs):
		job = frappe.get_doc(
			{
				"doctype": "Audio Separation Job",
				"user": user or frappe.session.user,
				"status": status,
				"original_file": self._create_file().file_url,
				"duration_seconds": duration_seconds,
				**kwargs,
			}
		)
		job.insert(ignore_permissions=True)
		return job

	def _set_job_modified_days_ago(self, job_name: str, days: int):
		old_modified = add_days(now_datetime(), -days)
		frappe.db.sql(
			"UPDATE `tabAudio Separation Job` SET modified = %s WHERE name = %s",
			(old_modified, job_name),
		)
		frappe.db.commit()

	def _create_stem_file(self, job, stem_type: str, content=b"stem-audio") -> str:
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{job.name}-{stem_type}.mp3",
				"attached_to_doctype": job.doctype,
				"attached_to_name": job.name,
				"is_private": 1,
				"content": content,
			}
		)
		file_doc.save(ignore_permissions=True)
		return file_doc.file_url

	def test_retry_only_allowed_for_failed_jobs(self):
		job = self._create_job(status="Failed", error_message="Previous failure")
		with patch("audio_stem.api.separation.frappe.enqueue"):
			result = retry_failed_job(job.name)
		self.assertEqual(result["status"], "Queued")
		job.reload()
		self.assertEqual(job.status, "Queued")
		self.assertIsNone(job.error_message)

	def test_retry_rejects_completed_jobs(self):
		job = self._create_job(status="Completed")
		with self.assertRaises(frappe.ValidationError):
			retry_failed_job(job.name)

	def test_retry_rejects_active_jobs(self):
		job = self._create_job(status="Processing")
		result = retry_failed_job(job.name)
		self.assertTrue(result["already_active"])

	def test_retry_respects_active_job_limit(self):
		user = "test-retry-user@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user,
					"first_name": "Retry",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)

		active = self._create_job(status="Processing", user=user)
		failed = self._create_job(status="Failed", user=user, error_message="Failed")

		frappe.set_user(user)
		with self.assertRaises(frappe.ValidationError):
			retry_failed_job(failed.name)

		frappe.set_user("Administrator")

	def test_retry_does_not_duplicate_old_credit_reservation(self):
		self._enable_credit_management()
		job = self._create_job(
			status="Failed",
			credit_status="Released",
			credit_reservation="OLD-RES",
			reserved_amount=0.03,
			error_message="Failed",
			credit_error="Old credit error",
		)

		with patch(f"{CLIENT}.credit_management_available", return_value=True), patch(
			f"{CLIENT}.get_user_credit_balance", return_value=self._mock_balance()
		), patch("credit_management.api.get_balance", return_value=self._mock_balance()), patch(
			"credit_management.api.reserve_credits", return_value=self._mock_reserve(0.03)
		) as reserve_mock, patch("audio_stem.api.separation.frappe.enqueue"):
			retry_failed_job(job.name)

		job.reload()
		self.assertEqual(job.credit_status, "Reserved")
		self.assertEqual(reserve_mock.call_count, 1)
		self.assertNotEqual(job.credit_reservation, "OLD-RES")
		self.assertIsNone(job.credit_error)

	def test_retry_keeps_old_output_urls_until_new_success(self):
		job = self._create_job(
			status="Failed",
			vocal_output_url="https://example.com/old-vocal.mp3",
			instrumental_output_url="https://example.com/old-instrumental.mp3",
			error_message="Failed",
		)
		with patch("audio_stem.api.separation.frappe.enqueue"):
			retry_failed_job(job.name)

		job.reload()
		self.assertEqual(job.vocal_output_url, "https://example.com/old-vocal.mp3")
		self.assertEqual(job.instrumental_output_url, "https://example.com/old-instrumental.mp3")

	def test_start_rejects_failed_jobs(self):
		job = self._create_job(status="Failed")
		with self.assertRaises(frappe.ValidationError):
			start_separation(job.name)

	def test_zip_returns_existing_archive_without_recreating_stems(self):
		job = self._create_job(status="Completed")
		job.vocal_file = self._create_stem_file(job, "vocal", b"vocal-track")
		job.instrumental_file = self._create_stem_file(job, "instrumental", b"instrumental-track")
		job.save(ignore_permissions=True)

		first = create_job_zip(job.name)
		job.reload()
		self.assertTrue(job.zip_file)

		job.vocal_file = None
		job.instrumental_file = None
		job.vocal_output_url = None
		job.instrumental_output_url = None
		job.save(ignore_permissions=True)

		second = create_job_zip(job.name)
		self.assertEqual(first["zip_file"], second["zip_file"])

	def test_zip_falls_back_to_output_url_when_local_file_missing(self):
		job = self._create_job(status="Completed")
		job.vocal_file = "/private/files/missing-vocal.mp3"
		job.instrumental_file = "/private/files/missing-instrumental.mp3"
		job.vocal_output_url = self._create_stem_file(job, "vocal", b"vocal-fallback")
		job.instrumental_output_url = self._create_stem_file(job, "instrumental", b"instrumental-fallback")
		job.save(ignore_permissions=True)

		result = create_job_zip(job.name)
		file_doc = frappe.get_doc("File", {"file_url": result["zip_file"]})
		with zipfile.ZipFile(file_doc.get_full_path(), "r") as archive:
			contents = {name: archive.read(name) for name in archive.namelist()}
		self.assertIn(b"vocal-fallback", contents.values())
		self.assertIn(b"instrumental-fallback", contents.values())

	def test_zip_works_with_external_cloudfront_output_urls(self):
		job = self._create_job(status="Completed")
		job.vocal_output_url = (
			"https://d2h7xmz5gqybh9.cloudfront.net/output/test_vocals.mp3"
		)
		job.instrumental_output_url = (
			"https://d2h7xmz5gqybh9.cloudfront.net/output/test_instrumental.mp3"
		)
		job.save(ignore_permissions=True)

		vocal_bytes = b"vocal-cloudfront"
		instrumental_bytes = b"instrumental-cloudfront"

		def fake_get(url, timeout=120):
			class Response:
				status_code = 200
				headers = {"Content-Type": "audio/mpeg"}

				def raise_for_status(self):
					return None

				@property
				def content(self):
					if "vocals" in url:
						return vocal_bytes
					return instrumental_bytes

			return Response()

		with patch("audio_stem.utils.zip_download.requests.get", side_effect=fake_get):
			result = create_job_zip(job.name)

		self.assertTrue(result["zip_file"])
		file_doc = frappe.get_doc("File", {"file_url": result["zip_file"]})
		with zipfile.ZipFile(file_doc.get_full_path(), "r") as archive:
			contents = {name: archive.read(name) for name in archive.namelist()}
		self.assertIn(vocal_bytes, contents.values())
		self.assertIn(instrumental_bytes, contents.values())

	def test_zip_only_allowed_for_completed_jobs(self):
		job = self._create_job(status="Failed")
		with self.assertRaises(frappe.ValidationError):
			create_job_zip(job.name)

	def test_user_cannot_create_zip_for_another_users_job(self):
		owner = "zip-owner@example.com"
		if not frappe.db.exists("User", owner):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": owner,
					"first_name": "Zip",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)

		job = self._create_job(status="Completed", user=owner)
		job.vocal_file = self._create_stem_file(job, "vocal")
		job.instrumental_file = self._create_stem_file(job, "instrumental")
		job.save(ignore_permissions=True)

		other = "zip-other@example.com"
		if not frappe.db.exists("User", other):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": other,
					"first_name": "Other",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)

		frappe.set_user(other)
		with self.assertRaises(frappe.PermissionError):
			create_job_zip(job.name)
		frappe.set_user("Administrator")

	def test_system_manager_can_create_zip(self):
		job = self._create_job(status="Completed")
		job.vocal_file = self._create_stem_file(job, "vocal", b"vocal-bytes")
		job.instrumental_file = self._create_stem_file(job, "instrumental", b"instrumental-bytes")
		job.save(ignore_permissions=True)

		result = create_job_zip(job.name)
		self.assertTrue(result["zip_file"])
		job.reload()
		self.assertTrue(job.zip_file)

	def test_zip_includes_vocal_and_instrumental(self):
		job = self._create_job(status="Completed")
		job.vocal_file = self._create_stem_file(job, "vocal", b"vocal-track")
		job.instrumental_file = self._create_stem_file(job, "instrumental", b"instrumental-track")
		job.save(ignore_permissions=True)

		result = create_job_zip(job.name)
		file_doc = frappe.get_doc("File", {"file_url": result["zip_file"]})
		zip_path = file_doc.get_full_path()
		with zipfile.ZipFile(zip_path, "r") as archive:
			contents = {name: archive.read(name) for name in archive.namelist()}
		self.assertEqual(len(contents), 2)
		self.assertIn(b"vocal-track", contents.values())
		self.assertIn(b"instrumental-track", contents.values())

	def test_zip_creation_failure_does_not_expose_traceback(self):
		job = self._create_job(status="Completed")
		job.vocal_file = self._create_stem_file(job, "vocal")
		job.instrumental_file = self._create_stem_file(job, "instrumental")
		job.save(ignore_permissions=True)

		with patch(
			"audio_stem.utils.zip_download.zipfile.ZipFile",
			side_effect=RuntimeError("Traceback (most recent call last): secret"),
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				create_job_zip(job.name)
		self.assertNotIn("Traceback", str(ctx.exception))

	def test_cleanup_does_nothing_when_disabled(self):
		job = self._create_job(status="Completed", completed_at=add_days(now_datetime(), -30))
		self._set_job_modified_days_ago(job.name, 30)
		result = cleanup_old_audio_jobs()
		self.assertTrue(result["skipped"])
		job.reload()
		self.assertTrue(job.original_file)

	def test_cleanup_respects_retention_days(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.cleanup_enabled = 1
		settings.retention_days = 7
		settings.delete_original_after_completion = 1
		settings.save(ignore_permissions=True)

		old_job = self._create_job(status="Completed", completed_at=add_days(now_datetime(), -30))
		self._set_job_modified_days_ago(old_job.name, 30)
		new_job = self._create_job(status="Completed", completed_at=now_datetime())

		cleanup_old_audio_jobs()

		old_job.reload()
		new_job.reload()
		self.assertFalse(old_job.original_file)
		self.assertTrue(new_job.original_file)

	def test_cleanup_does_not_delete_job_records(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.cleanup_enabled = 1
		settings.retention_days = 1
		settings.save(ignore_permissions=True)

		job = self._create_job(status="Failed", completed_at=add_days(now_datetime(), -10))
		self._set_job_modified_days_ago(job.name, 10)
		cleanup_old_audio_jobs()
		self.assertTrue(frappe.db.exists("Audio Separation Job", job.name))

	def test_cleanup_can_delete_local_outputs_after_retention(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.cleanup_enabled = 1
		settings.retention_days = 1
		settings.delete_outputs_after_retention = 1
		settings.save(ignore_permissions=True)

		job = self._create_job(status="Completed", completed_at=add_days(now_datetime(), -10))
		job.vocal_file = self._create_stem_file(job, "vocal")
		job.instrumental_file = self._create_stem_file(job, "instrumental")
		job.vocal_output_url = "https://example.com/vocal.mp3"
		job.instrumental_output_url = "https://example.com/instrumental.mp3"
		job.save(ignore_permissions=True)
		self._set_job_modified_days_ago(job.name, 10)

		cleanup_old_audio_jobs()

		job.reload()
		self.assertFalse(job.vocal_file)
		self.assertFalse(job.instrumental_file)
		self.assertFalse(job.vocal_output_url)
		self.assertFalse(job.instrumental_output_url)

	def test_cleanup_is_idempotent(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.cleanup_enabled = 1
		settings.retention_days = 1
		settings.delete_original_after_completion = 1
		settings.save(ignore_permissions=True)

		job = self._create_job(status="Completed", completed_at=add_days(now_datetime(), -10))
		self._set_job_modified_days_ago(job.name, 10)

		cleanup_old_audio_jobs()
		cleanup_old_audio_jobs()

		job.reload()
		self.assertFalse(job.original_file)
		self.assertTrue(job.cleanup_notes)

	def test_system_manager_can_access_usage_summary(self):
		summary = get_audio_stem_usage_summary()
		self.assertIn("total_jobs", summary)
		self.assertIn("completed_jobs", summary)
		self.assertIn("failed_jobs", summary)
		self.assertIn("total_duration_seconds", summary)
		self.assertIn("total_provider_cost_usd", summary)

	def test_normal_user_cannot_access_global_usage_summary(self):
		user = "metrics-user@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user,
					"first_name": "Metrics",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)

		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			get_audio_stem_usage_summary()
		frappe.set_user("Administrator")

	def test_usage_report_includes_core_metrics(self):
		self._create_job(status="Completed", duration_seconds=30, provider_cost_usd=0.03)
		self._create_job(status="Failed")
		columns, data = execute()
		metrics = {row["metric"]: row["value"] for row in data if row.get("metric")}
		self.assertGreaterEqual(int(metrics["Total Jobs"]), 2)
		self.assertGreaterEqual(int(metrics["Completed Jobs"]), 1)
		self.assertGreaterEqual(int(metrics["Failed Jobs"]), 1)

	def test_get_recent_jobs_includes_milestone5_fields(self):
		job = self._create_job(
			status="Failed",
			original_filename="song.mp3",
			error_message="Safe failure",
			provider_cost_usd=0.03,
		)
		rows = get_recent_jobs(limit=5)
		match = next(row for row in rows if row["name"] == job.name)
		self.assertEqual(match["original_filename"], "song.mp3")
		self.assertEqual(match["error_summary"], "Safe failure")
		self.assertTrue(match["can_retry"])

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

	def _enable_credit_management(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.credit_management_enabled = 1
		settings.save(ignore_permissions=True)

	def _mock_balance(self, available_balance=100):
		return {
			"current_balance": available_balance,
			"reserved_balance": 0,
			"available_balance": available_balance,
		}

	def _mock_reserve(self, amount):
		return {
			"reservation": "RES-TEST-001",
			"reserved_amount": amount,
			"credit_type": "AUDIO_STEM",
			"available_balance": 0,
		}
