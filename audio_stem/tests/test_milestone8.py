# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from audio_stem.api.separation import (
	download_transcript_asset,
	get_job_detail,
	start_karaoke_render,
	start_transcription,
)
from audio_stem.integrations.openai_transcription_client import (
	is_openai_transcription_enabled,
	transcribe_with_whisper,
)
from audio_stem.tests.base import AudioStemTestCase
from audio_stem.utils.config_checklist import get_configuration_checklist_data
from audio_stem.utils.karaoke_subtitles import build_karaoke_words_json, write_karaoke_json
from audio_stem.utils.provider_health import get_provider_health_summary
from audio_stem.utils.transcription_assets import (
	estimate_transcription_cost,
	write_srt_from_segments_or_words,
	write_transcript_json,
	write_vtt_from_segments_or_words,
)
from audio_stem.workers.karaoke_worker import process_karaoke_render
from audio_stem.workers.transcription_worker import process_transcription

APP_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_CREDIT_PATTERNS = (
	"Credit Account",
	"Credit Ledger Entry",
	"Credit Ledger",
	"tabCredit Account",
	"tabCredit Ledger Entry",
)
SAMPLE_TRANSCRIPT = {
	"text": "hello world",
	"language": "en",
	"duration": 5.0,
	"segments": [{"id": 0, "start": 0.0, "end": 2.5, "text": "hello"}, {"id": 1, "start": 2.5, "end": 5.0, "text": "world"}],
	"words": [
		{"word": "hello", "start": 0.0, "end": 1.0},
		{"word": "world", "start": 2.5, "end": 5.0},
	],
}


class TestAudioSeparationMilestone8(AudioStemTestCase):
	def _enable_openai(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.openai_enabled = 1
		settings.openai_api_key = "test-openai-key"
		settings.transcription_model = "whisper-1"
		settings.enable_word_timestamps = 1
		settings.save(ignore_permissions=True)

	def _enable_karaoke(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_enabled = 1
		settings.karaoke_ass_enabled = 1
		settings.karaoke_video_render_enabled = 0
		settings.karaoke_style_preset = "default_1080p"
		settings.save(ignore_permissions=True)

	def test_checklist_does_not_expose_openai_api_key(self):
		self._enable_openai()
		items = get_configuration_checklist_data()
		payload = json.dumps(items)
		self.assertNotIn("test-openai-key", payload)
		self.assertNotIn("sk-", payload)

	def test_missing_openai_key_blocks_transcription(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.openai_enabled = 1
		settings.openai_api_key = ""
		settings.save(ignore_permissions=True)
		frappe.db.set_value(
			"Audio Separation Settings",
			"Audio Separation Settings",
			"openai_api_key",
			"",
		)
		job = self._create_completed_job()
		with self.assertRaises(frappe.ValidationError):
			start_transcription(job.name)

	def test_openai_disabled_blocks_transcription(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.openai_enabled = 0
		settings.save(ignore_permissions=True)
		job = self._create_completed_job()
		with self.assertRaises(frappe.ValidationError):
			start_transcription(job.name)

	def test_owner_can_start_transcription(self):
		self._enable_openai()
		job = self._create_completed_job()
		with patch("audio_stem.api.separation.frappe.enqueue"):
			result = start_transcription(job.name)
		self.assertFalse(result.get("already_active"))
		self.assertEqual(result["transcription_status"], "Queued")

	def test_another_user_cannot_start_transcription(self):
		self._enable_openai()
		owner = self._ensure_user(f"tx-owner-{frappe.generate_hash(length=6)}@example.com")
		other = self._ensure_user(f"tx-other-{frappe.generate_hash(length=6)}@example.com")
		job = self._create_completed_job(user=owner)
		frappe.set_user(other)
		with self.assertRaises(frappe.PermissionError):
			start_transcription(job.name)

	def test_system_manager_can_start_transcription(self):
		self._enable_openai()
		owner = self._ensure_user(f"tx-owner2-{frappe.generate_hash(length=6)}@example.com")
		job = self._create_completed_job(user=owner)
		frappe.set_user("Administrator")
		with patch("audio_stem.api.separation.frappe.enqueue"):
			start_transcription(job.name)

	def test_cannot_transcribe_vocal_before_separation_completed(self):
		self._enable_openai()
		job = frappe.get_doc(
			{
				"doctype": "Audio Separation Job",
				"user": frappe.session.user,
				"status": "Draft",
				"original_file": self._create_file().file_url,
				"duration_seconds": 30,
			}
		)
		job.insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			start_transcription(job.name, source="Vocal")

	def test_duplicate_transcription_start_does_not_enqueue_twice(self):
		self._enable_openai()
		job = self._create_completed_job()
		job.transcription_status = "Processing"
		job.save(ignore_permissions=True)
		with patch("audio_stem.api.separation.frappe.enqueue") as enqueue_mock:
			result = start_transcription(job.name)
		self.assertTrue(result.get("already_active"))
		enqueue_mock.assert_not_called()

	def test_failed_transcription_can_be_retried(self):
		self._enable_openai()
		job = self._create_completed_job()
		job.transcription_status = "Failed"
		job.save(ignore_permissions=True)
		with patch("audio_stem.api.separation.frappe.enqueue"):
			result = start_transcription(job.name)
		self.assertEqual(result["transcription_status"], "Queued")

	def test_completed_transcription_is_not_duplicated(self):
		self._enable_openai()
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		job.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			start_transcription(job.name)

	@patch("openai.OpenAI")
	def test_whisper_wrapper_uses_verbose_json_and_word_timestamps(self, openai_mock):
		self._enable_openai()
		client = MagicMock()
		openai_mock.return_value = client
		response = MagicMock()
		response.text = "hello"
		response.language = "en"
		response.duration = 1.0
		response.segments = []
		response.words = [{"word": "hello", "start": 0.0, "end": 1.0}]
		response.model_dump.return_value = {
			"text": "hello",
			"language": "en",
			"duration": 1.0,
			"segments": [],
			"words": [{"word": "hello", "start": 0.0, "end": 1.0}],
		}
		client.audio.transcriptions.create.return_value = response

		with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
			tmp.write(b"audio")
			path = tmp.name

		result = transcribe_with_whisper(path)
		os.unlink(path)
		kwargs = client.audio.transcriptions.create.call_args.kwargs
		self.assertEqual(kwargs["model"], "whisper-1")
		self.assertEqual(kwargs["response_format"], "verbose_json")
		self.assertEqual(kwargs["timestamp_granularities"], ["word", "segment"])
		self.assertEqual(result["text"], "hello")
		self.assertEqual(len(result["words"]), 1)

	def test_whisper_safe_error_does_not_expose_api_key(self):
		self._enable_openai()
		with patch("audio_stem.integrations.openai_transcription_client.get_openai_client") as client_mock:
			client_mock.return_value.audio.transcriptions.create.side_effect = Exception(
				"openai api_key=secret-key"
			)
			with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
				tmp.write(b"audio")
				path = tmp.name
			with self.assertRaises(frappe.ValidationError) as ctx:
				transcribe_with_whisper(path)
			os.unlink(path)
			self.assertNotIn("secret-key", str(ctx.exception))

	def test_transcript_json_is_private(self):
		job = self._create_completed_job()
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		file_name = frappe.db.get_value("File", {"file_url": job.transcript_json_file}, "name")
		file_doc = frappe.get_doc("File", file_name)
		self.assertEqual(file_doc.is_private, 1)

	def test_srt_and_vtt_generated(self):
		job = self._create_completed_job()
		write_srt_from_segments_or_words(job, SAMPLE_TRANSCRIPT)
		write_vtt_from_segments_or_words(job, SAMPLE_TRANSCRIPT)
		self.assertTrue(job.transcript_srt_file)
		self.assertTrue(job.transcript_vtt_file)

	def test_word_timestamps_preserved_in_karaoke_json(self):
		job = self._create_completed_job()
		karaoke = build_karaoke_words_json(job, SAMPLE_TRANSCRIPT)
		self.assertEqual(karaoke["words"][0]["text"], "hello")
		self.assertIn("start", karaoke["words"][0])

	def test_segment_fallback_when_words_missing(self):
		job = self._create_completed_job()
		data = dict(SAMPLE_TRANSCRIPT)
		data["words"] = []
		karaoke = build_karaoke_words_json(job, data)
		self.assertTrue(karaoke["words"])
		self.assertEqual(karaoke["words"][0]["text"], "hello")

	def test_karaoke_disabled_blocks_render(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_enabled = 0
		settings.save(ignore_permissions=True)
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		job.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			start_karaoke_render(job.name)

	def test_karaoke_requires_completed_transcription(self):
		self._enable_karaoke()
		job = self._create_completed_job()
		with self.assertRaises(frappe.ValidationError):
			start_karaoke_render(job.name)

	def test_duplicate_karaoke_render_does_not_enqueue_twice(self):
		self._enable_karaoke()
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		job.karaoke_status = "Rendering"
		job.save(ignore_permissions=True)
		with patch("audio_stem.api.separation.frappe.enqueue") as enqueue_mock:
			result = start_karaoke_render(job.name)
		self.assertTrue(result.get("already_active"))
		enqueue_mock.assert_not_called()

	@patch("audio_stem.workers.karaoke_worker.render_karaoke_video_with_engine")
	@patch("audio_stem.workers.karaoke_worker.build_karaoke_ass_with_engine")
	def test_karaoke_engine_failure_stores_safe_karaoke_error(self, ass_mock, render_mock):
		self._enable_karaoke()
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		job.transcript_text = "hello"
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		job.save(ignore_permissions=True)
		ass_mock.side_effect = Exception("openai api_key leaked traceback")
		with self.assertRaises(Exception):
			process_karaoke_render(job.name)
		job.reload()
		self.assertEqual(job.karaoke_status, "Failed")
		self.assertNotIn("api_key", job.karaoke_error or "")

	@patch("audio_stem.workers.karaoke_worker.render_karaoke_video_with_engine")
	@patch("audio_stem.workers.karaoke_worker.build_karaoke_ass_with_engine")
	def test_completed_karaoke_ass_attached_privately(self, ass_mock, render_mock):
		self._enable_karaoke()
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		job.save(ignore_permissions=True)

		def _attach_ass(job, **kwargs):
			file_doc = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": f"{job.name}-karaoke.ass",
					"is_private": 1,
					"content": b"[Script Info]",
				}
			)
			file_doc.save(ignore_permissions=True)
			job.karaoke_ass_file = file_doc.file_url
			job.save(ignore_permissions=True)
			return file_doc.file_url

		ass_mock.side_effect = _attach_ass
		process_karaoke_render(job.name)
		job.reload()
		self.assertEqual(job.karaoke_status, "Completed")
		self.assertTrue(job.karaoke_ass_file)
		file_name = frappe.db.get_value("File", {"file_url": job.karaoke_ass_file}, "name")
		self.assertEqual(frappe.db.get_value("File", file_name, "is_private"), 1)
		render_mock.assert_not_called()

	@patch("audio_stem.workers.karaoke_worker.render_karaoke_video_with_engine")
	@patch("audio_stem.workers.karaoke_worker.build_karaoke_ass_with_engine")
	def test_old_karaoke_file_not_overwritten_until_success(self, ass_mock, render_mock):
		self._enable_karaoke()
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_video_render_enabled = 1
		settings.save(ignore_permissions=True)
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		old_file = self._create_file().file_url
		job.karaoke_video_file = old_file
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		job.save(ignore_permissions=True)
		ass_mock.return_value = self._create_file().file_url
		render_mock.side_effect = Exception("render failed")
		process_karaoke_render(job.name)
		job.reload()
		self.assertEqual(job.karaoke_video_file, old_file)
		self.assertEqual(job.karaoke_status, "Completed")
		self.assertIn("Video render failed", job.karaoke_error or "")

	def test_job_detail_includes_transcription_and_karaoke_fields(self):
		self._enable_openai()
		self._enable_karaoke()
		job = self._create_completed_job()
		detail = get_job_detail(job.name)
		self.assertIn("transcription_status", detail)
		self.assertIn("karaoke_status", detail)
		self.assertIn("karaoke_ass_file", detail)
		self.assertNotIn("openai_api_key", detail)
		self.assertNotIn("PyCaps", str(detail))
		self.assertNotIn("pycaps", str(detail).lower())

	def test_download_transcript_asset_returns_private_file(self):
		job = self._create_completed_job()
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		job.save(ignore_permissions=True)
		result = download_transcript_asset(job.name, "json")
		self.assertEqual(result["asset_type"], "json")
		self.assertTrue(result["file_url"])

	def test_estimate_transcription_cost(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.transcription_cost_per_minute_usd = 0.006
		settings.save(ignore_permissions=True)
		self.assertEqual(estimate_transcription_cost(60), 0.006)

	def test_provider_health_includes_transcription_and_karaoke_stats(self):
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		job.karaoke_status = "Failed"
		job.save(ignore_permissions=True)
		summary = get_provider_health_summary()
		self.assertIn("transcription_completed_count", summary)
		self.assertIn("karaoke_failed_count", summary)
		self.assertIn("karaoke_ass_completed_count", summary)
		self.assertIn("karaoke_video_completed_count", summary)

	@patch("audio_stem.workers.transcription_worker.transcribe_with_whisper")
	@patch("audio_stem.workers.transcription_worker.resolve_transcription_source_path")
	@patch("audio_stem.workers.transcription_worker.prepare_audio_for_whisper")
	def test_transcription_worker_completes(self, prepare_mock, resolve_mock, transcribe_mock):
		self._enable_openai()
		job = self._create_completed_job()
		resolve_mock.return_value = "/tmp/audio.mp3"
		prepare_mock.return_value = ("/tmp/audio.mp3", False)
		transcribe_mock.return_value = SAMPLE_TRANSCRIPT
		process_transcription(job.name, source="Vocal")
		job.reload()
		self.assertEqual(job.transcription_status, "Completed")
		self.assertTrue(job.transcript_text)

	def test_credit_boundary_static_guard_still_passes(self):
		forbidden_hits = []
		for path in APP_ROOT.rglob("*.py"):
			if "tests" in path.parts:
				continue
			text = path.read_text(encoding="utf-8", errors="ignore")
			for pattern in FORBIDDEN_CREDIT_PATTERNS:
				if pattern in text and "credit_management_client" not in str(path):
					for line_no, line in enumerate(text.splitlines(), start=1):
						if pattern in line and "FORBIDDEN" not in line:
							forbidden_hits.append(f"{path}:{line_no}:{line.strip()}")
		self.assertEqual(forbidden_hits, [])

	def test_is_openai_transcription_enabled(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.openai_enabled = 1
		settings.save(ignore_permissions=True)
		self.assertTrue(is_openai_transcription_enabled())
