# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
import tempfile
import uuid
from io import BytesIO
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.datastructures import FileStorage

from audio_stem.api.separation import (
	_save_uploaded_audio,
	create_job_from_file,
	get_job_status,
	get_recent_jobs,
	start_separation,
)
from audio_stem.integrations.wavespeed_client import SeparationResult, isolate_vocal_and_instrumental
from audio_stem.tests.base import AudioStemTestCase
from audio_stem.workers.separation_worker import process_audio_separation


class TestAudioSeparation(AudioStemTestCase):
	def _create_job(self, with_file: bool = True):
		job = frappe.get_doc(
			{
				"doctype": "Audio Separation Job",
				"user": frappe.session.user,
				"status": "Draft",
			}
		)
		if with_file:
			job.original_file = self._attach_test_file()
			job.duration_seconds = 30
		job.insert(ignore_permissions=True)
		return job

	def _attach_test_file(self):
		with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
			tmp.write(b"fake-audio-content")
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
		return file_doc.file_url

	def test_cannot_start_without_original_file(self):
		job = self._create_job(with_file=False)
		with self.assertRaises(frappe.ValidationError):
			start_separation(job.name)

	def test_queued_status_after_start(self):
		job = self._create_job()
		with patch("audio_stem.api.separation.frappe.enqueue") as enqueue_mock:
			result = start_separation(job.name)

		job.reload()
		self.assertEqual(result["status"], "Queued")
		self.assertEqual(job.status, "Queued")
		self.assertEqual(job.provider, "WaveSpeed")
		self.assertEqual(job.provider_model, "wavespeed-ai/audio-vocal-isolator")
		self.assertGreater(job.provider_cost_usd, 0)
		enqueue_mock.assert_called_once()

	def test_output_order_mapping(self):
		vocal_url = "https://example.com/vocal.mp3"
		instrumental_url = "https://example.com/instrumental.mp3"
		mock_client = type(
			"MockClient",
			(),
			{
				"upload": lambda self, path: "https://example.com/uploaded.mp3",
				"run": lambda self, model, payload: {
					"outputs": [vocal_url, instrumental_url]
				},
			},
		)()

		with patch("audio_stem.integrations.wavespeed_client._get_api_key", return_value="test-api-key"), patch(
			"wavespeed.Client", return_value=mock_client
		):
			result = isolate_vocal_and_instrumental("/tmp/fake.mp3")

		self.assertEqual(result.vocal_url, vocal_url)
		self.assertEqual(result.instrumental_url, instrumental_url)

	def test_worker_completes_with_mocked_wavespeed(self):
		job = self._create_job()
		start_separation(job.name)

		vocal_url = "https://example.com/vocal.mp3"
		instrumental_url = "https://example.com/instrumental.mp3"

		with patch(
			"audio_stem.workers.separation_worker.isolate_vocal_and_instrumental",
			return_value=SeparationResult(
				vocal_url=vocal_url,
				instrumental_url=instrumental_url,
			),
		):
			process_audio_separation(job.name)

		job.reload()
		self.assertEqual(job.status, "Completed")
		self.assertEqual(job.vocal_output_url, vocal_url)
		self.assertEqual(job.instrumental_output_url, instrumental_url)


class TestAudioSeparationPageAPI(AudioStemTestCase):
	def setUp(self):
		super().setUp()
		self.suffix = uuid.uuid4().hex[:8]
		self.user_a = f"audio-user-a-{self.suffix}@example.com"
		self.user_b = f"audio-user-b-{self.suffix}@example.com"
		for email in (self.user_a, self.user_b):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": email.split("@")[0],
						"send_welcome_email": 0,
					}
				).insert(ignore_permissions=True)

	def _upload_file_as(self, user: str):
		frappe.set_user(user)
		with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
			tmp.write(b"fake-audio-content")
			tmp_path = tmp.name

		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{user}-{os.path.basename(tmp_path)}",
				"is_private": 1,
				"content": open(tmp_path, "rb").read(),
			}
		)
		file_doc.save(ignore_permissions=True)
		os.unlink(tmp_path)
		return file_doc.file_url

	def _mock_audio_upload(self, user: str, filename: str = "sample.mp3"):
		frappe.set_user(user)
		upload = FileStorage(
			stream=BytesIO(b"fake-audio-content"),
			filename=filename,
			content_type="audio/mpeg",
		)
		return _save_uploaded_audio(upload)

	def test_upload_audio_file_rejects_non_audio(self):
		frappe.set_user(self.user_a)
		upload = FileStorage(
			stream=BytesIO(b"not-audio"),
			filename="notes.txt",
			content_type="text/plain",
		)
		with self.assertRaises(frappe.ValidationError):
			_save_uploaded_audio(upload)

	def test_upload_audio_file_returns_file_url(self):
		result = self._mock_audio_upload(self.user_a)
		self.assertTrue(result["file_url"])
		self.assertEqual(result["file_name"], "sample.mp3")

	def test_create_job_from_file_requires_login(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			create_job_from_file("/private/files/test.mp3")

	def test_create_job_from_file_sets_session_user(self):
		file_url = self._upload_file_as(self.user_a)
		result = create_job_from_file(file_url)
		job = frappe.get_doc("Audio Separation Job", result["name"])
		self.assertEqual(job.user, self.user_a)
		self.assertEqual(result["original_file"], file_url)

	def test_get_job_status_rejects_other_users_job(self):
		file_url = self._upload_file_as(self.user_a)
		job_name = create_job_from_file(file_url)["name"]

		frappe.set_user(self.user_b)
		with self.assertRaises(frappe.PermissionError):
			get_job_status(job_name)

	def test_get_recent_jobs_returns_only_current_users_jobs(self):
		file_a = self._upload_file_as(self.user_a)
		job_a = create_job_from_file(file_a)["name"]
		file_b = self._upload_file_as(self.user_b)
		create_job_from_file(file_b)

		frappe.set_user(self.user_a)
		recent = get_recent_jobs(limit=20)
		names = {row["name"] for row in recent}
		self.assertIn(job_a, names)
		self.assertTrue(all(row.get("name") for row in recent))

		for row in recent:
			owner = frappe.db.get_value("Audio Separation Job", row["name"], "user")
			self.assertEqual(owner, self.user_a)
