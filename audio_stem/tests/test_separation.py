# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
import tempfile
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from audio_stem.api.separation import start_separation
from audio_stem.integrations.wavespeed_client import SeparationResult, isolate_vocal_and_instrumental
from audio_stem.workers.separation_worker import process_audio_separation


class TestAudioSeparation(FrappeTestCase):
	def setUp(self):
		settings = frappe.get_single("Audio Separation Settings")
		self._saved_enabled = settings.enabled
		self._saved_api_key = settings.get_password("wavespeed_api_key", raise_exception=False)
		settings.enabled = 1
		settings.wavespeed_api_key = "test-api-key"
		settings.save(ignore_permissions=True)

	def tearDown(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.enabled = self._saved_enabled
		settings.wavespeed_api_key = self._saved_api_key or ""
		settings.save(ignore_permissions=True)

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
