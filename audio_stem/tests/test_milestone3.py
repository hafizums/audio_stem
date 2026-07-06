# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
import tempfile
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from audio_stem.api.separation import create_job_from_file, start_separation
from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.limits import calculate_provider_cost
from audio_stem.workers.separation_worker import process_audio_separation


class TestAudioSeparationMilestone3(FrappeTestCase):
	def setUp(self):
		settings = frappe.get_single("Audio Separation Settings")
		self._saved = {
			"enabled": settings.enabled,
			"wavespeed_api_key": settings.get_password("wavespeed_api_key", raise_exception=False),
			"max_file_size_mb": settings.max_file_size_mb,
			"max_audio_duration_seconds": settings.max_audio_duration_seconds,
			"cost_per_second_usd": settings.cost_per_second_usd,
			"store_outputs_locally": settings.store_outputs_locally,
		}
		settings.enabled = 1
		settings.wavespeed_api_key = "test-api-key"
		settings.max_file_size_mb = 50
		settings.max_audio_duration_seconds = 600
		settings.cost_per_second_usd = 0.001
		settings.store_outputs_locally = 0
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

	def _create_file(self, content: bytes, suffix: str = ".mp3"):
		with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
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

	def _create_draft_job(self, duration_seconds=30, file_url=None):
		if not file_url:
			file_url = self._create_file(b"audio").file_url
		job = frappe.get_doc(
			{
				"doctype": "Audio Separation Job",
				"user": frappe.session.user,
				"status": "Draft",
				"original_file": file_url,
				"duration_seconds": duration_seconds,
			}
		)
		job.insert(ignore_permissions=True)
		return job

	def test_disabled_settings_blocks_start(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.enabled = 0
		settings.save(ignore_permissions=True)

		job = self._create_draft_job()
		with self.assertRaises(frappe.ValidationError):
			start_separation(job.name)

	def test_max_duration_blocks_start(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.max_audio_duration_seconds = 10
		settings.save(ignore_permissions=True)

		job = self._create_draft_job(duration_seconds=30)
		with self.assertRaises(frappe.ValidationError):
			start_separation(job.name)

	def test_max_file_size_blocks_start(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.max_file_size_mb = 1
		settings.save(ignore_permissions=True)

		large_content = b"x" * (2 * 1024 * 1024)
		file_doc = self._create_file(large_content)
		job = self._create_draft_job(duration_seconds=5, file_url=file_doc.file_url)

		with self.assertRaises(frappe.ValidationError):
			start_separation(job.name)

	def test_active_job_limit_blocks_second_start(self):
		email = f"audio-limit-{frappe.generate_hash(length=8)}@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Audio",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)

		frappe.set_user(email)
		job_a = self._create_draft_job()
		job_b = self._create_draft_job()

		with patch("audio_stem.api.separation.frappe.enqueue"):
			start_separation(job_a.name)

		with self.assertRaises(frappe.ValidationError):
			start_separation(job_b.name)

	def test_repeated_start_does_not_enqueue_duplicate_job(self):
		job = self._create_draft_job()
		with patch("audio_stem.api.separation.frappe.enqueue") as enqueue_mock:
			first = start_separation(job.name)
			second = start_separation(job.name)

		self.assertFalse(first.get("already_active"))
		self.assertTrue(second.get("already_active"))
		enqueue_mock.assert_called_once()

	def test_completed_job_cannot_be_restarted(self):
		job = self._create_draft_job()
		job.status = "Completed"
		job.save(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			start_separation(job.name)

	def test_provider_cost_calculated_before_queueing(self):
		job = self._create_draft_job(duration_seconds=45)
		with patch("audio_stem.api.separation.frappe.enqueue"):
			result = start_separation(job.name)

		job.reload()
		expected = calculate_provider_cost(45)
		self.assertEqual(result["provider_cost_usd"], expected)
		self.assertEqual(job.provider_cost_usd, expected)

	def test_safe_error_message_is_stored_on_failure(self):
		job = self._create_draft_job()
		with patch(
			"audio_stem.workers.separation_worker.isolate_vocal_and_instrumental",
			side_effect=RuntimeError("HTTP 401: Authentication is required. api_key=secret"),
		):
			process_audio_separation(job.name)

		job.reload()
		self.assertEqual(job.status, "Failed")
		self.assertNotIn("secret", job.error_message or "")
		self.assertNotIn("api_key", (job.error_message or "").lower())
		self.assertEqual(
			job.error_message,
			safe_error_message(RuntimeError("HTTP 401: Authentication is required. api_key=secret")),
		)

	def test_create_job_blocks_when_disabled(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.enabled = 0
		settings.save(ignore_permissions=True)

		file_doc = self._create_file(b"audio")
		with self.assertRaises(frappe.ValidationError):
			create_job_from_file(file_doc.file_url)

	def test_create_job_blocks_oversized_file(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.max_file_size_mb = 1
		settings.save(ignore_permissions=True)

		file_doc = self._create_file(b"x" * (2 * 1024 * 1024))
		with self.assertRaises(frappe.ValidationError):
			create_job_from_file(file_doc.file_url)
