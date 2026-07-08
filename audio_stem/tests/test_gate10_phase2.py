# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
import tempfile
from unittest.mock import patch

import frappe
from frappe.utils import add_days, now_datetime

from audio_stem.api import admin as admin_api
from audio_stem.api.separation import cancel_job, get_job_detail, retry_failed_job, start_transcription
from audio_stem.integrations.wavespeed_client import SeparationResult
from audio_stem.tests.base import AudioStemTestCase
from audio_stem.utils.audit_log import log_audit
from audio_stem.utils.cleanup import cleanup_old_audio_jobs, cleanup_old_audit_logs
from audio_stem.utils.downstream_assets import (
	DEFAULT_DOWNSTREAM_STALE_REASON,
	invalidate_downstream_assets,
)
from audio_stem.workers.karaoke_worker import process_karaoke_render
from audio_stem.workers.separation_worker import process_audio_separation
from audio_stem.workers.transcription_worker import process_transcription

CREDIT_API = "credit_management.api"
CLIENT = "audio_stem.integrations.credit_management_client"


class TestGate10Phase2DownstreamStale(AudioStemTestCase):
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

	def _create_stem_file(self, job, stem_type: str, content=b"stem") -> str:
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

	def test_retry_success_marks_downstream_assets_stale(self):
		job = self._create_job()
		job.status = "Failed"
		job.transcription_status = "Completed"
		job.karaoke_status = "Completed"
		job.manual_transcript_status = "Approved"
		job.transcript_json_file = self._create_stem_file(job, "transcript.json", b"{}")
		job.karaoke_ass_file = self._create_stem_file(job, "karaoke.ass", b"[Script Info]")
		job.save(ignore_permissions=True)
		transcript_file = job.transcript_json_file
		ass_file = job.karaoke_ass_file

		with patch("audio_stem.api.separation.frappe.enqueue"):
			retry_failed_job(job.name)

		with patch(
			"audio_stem.workers.separation_worker.isolate_vocal_and_instrumental",
			return_value=SeparationResult(
				vocal_url="https://example.com/vocal-new.mp3",
				instrumental_url="https://example.com/instrumental-new.mp3",
			),
		):
			process_audio_separation(job.name)

		job.reload()
		self.assertEqual(job.status, "Completed")
		self.assertTrue(job.downstream_assets_stale)
		self.assertEqual(job.transcription_status, "Not Started")
		self.assertEqual(job.karaoke_status, "Not Started")
		self.assertEqual(job.manual_transcript_status, "Not Started")
		self.assertEqual(job.transcript_json_file, transcript_file)
		self.assertEqual(job.karaoke_ass_file, ass_file)

	def test_job_detail_exposes_stale_flag_safely(self):
		job = self._create_job()
		job.status = "Completed"
		job.downstream_assets_stale = 1
		job.downstream_stale_reason = DEFAULT_DOWNSTREAM_STALE_REASON
		job.transcription_status = "Not Started"
		job.transcript_json_file = self._create_stem_file(job, "transcript.json", b"{}")
		job.save(ignore_permissions=True)

		payload = get_job_detail(job.name)
		self.assertTrue(payload["downstream_assets_stale"])
		self.assertFalse(payload["has_current_transcript_json"])
		self.assertTrue(payload["has_transcript_json"])

	def test_new_transcription_clears_stale_flag(self):
		job = self._create_job()
		job.status = "Completed"
		job.downstream_assets_stale = 1
		job.downstream_stale_reason = DEFAULT_DOWNSTREAM_STALE_REASON
		job.transcription_status = "Not Started"
		job.vocal_output_url = "https://example.com/vocal.mp3"
		job.save(ignore_permissions=True)

		settings = frappe.get_single("Audio Separation Settings")
		settings.openai_enabled = 1
		settings.openai_api_key = "test-openai-key"
		settings.save(ignore_permissions=True)

		with patch(
			"audio_stem.workers.transcription_worker.transcribe_with_whisper",
			return_value={"text": "hello", "segments": [], "duration": 1},
		), patch(
			"audio_stem.workers.transcription_worker.write_transcript_json",
		), patch(
			"audio_stem.workers.transcription_worker.write_srt_from_segments_or_words",
		), patch(
			"audio_stem.workers.transcription_worker.write_vtt_from_segments_or_words",
		), patch(
			"audio_stem.workers.transcription_worker.resolve_transcription_source_path",
			return_value="/tmp/fake.mp3",
		), patch(
			"audio_stem.workers.transcription_worker.prepare_audio_for_whisper",
			return_value=("/tmp/fake.mp3", False),
		):
			process_transcription(job.name)

		job.reload()
		self.assertFalse(job.downstream_assets_stale)
		self.assertEqual(job.transcription_status, "Completed")


class TestGate10Phase2Cleanup(AudioStemTestCase):
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

	def _create_job(self, **kwargs):
		job = frappe.get_doc(
			{
				"doctype": "Audio Separation Job",
				"user": frappe.session.user,
				"status": "Completed",
				"original_file": self._create_file().file_url,
				"duration_seconds": 30,
				**kwargs,
			}
		)
		job.insert(ignore_permissions=True)
		return job

	def _create_attached_file(self, job, name: str, content=b"x") -> str:
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": name,
				"attached_to_doctype": job.doctype,
				"attached_to_name": job.name,
				"is_private": 1,
				"content": content,
			}
		)
		file_doc.save(ignore_permissions=True)
		return file_doc.file_url

	def _set_job_modified_days_ago(self, job_name: str, days: int):
		old_modified = add_days(now_datetime(), -days)
		frappe.db.sql(
			"UPDATE `tabAudio Separation Job` SET modified = %s WHERE name = %s",
			(old_modified, job_name),
		)
		frappe.db.commit()

	def _enable_cleanup(self, **extra):
		settings = frappe.get_single("Audio Separation Settings")
		settings.cleanup_enabled = 1
		settings.retention_days = 1
		for key, value in extra.items():
			setattr(settings, key, value)
		settings.save(ignore_permissions=True)

	def test_cleanup_deletes_transcripts_only_when_enabled(self):
		self._enable_cleanup(delete_transcripts_after_retention=1)
		job = self._create_job()
		job.transcript_json_file = self._create_attached_file(job, "t.json", b"{}")
		job.save(ignore_permissions=True)
		self._set_job_modified_days_ago(job.name, 10)

		cleanup_old_audio_jobs()
		job.reload()
		self.assertFalse(job.transcript_json_file)

	def test_cleanup_preserves_transcripts_when_disabled(self):
		self._enable_cleanup(delete_transcripts_after_retention=0)
		job = self._create_job()
		job.transcript_json_file = self._create_attached_file(job, "t.json", b"{}")
		job.save(ignore_permissions=True)
		self._set_job_modified_days_ago(job.name, 10)

		cleanup_old_audio_jobs()
		job.reload()
		self.assertTrue(job.transcript_json_file)

	def test_cleanup_deletes_zip_only_when_enabled(self):
		self._enable_cleanup(delete_zip_after_retention=1)
		job = self._create_job()
		job.zip_file = self._create_attached_file(job, "bundle.zip", b"zip")
		job.save(ignore_permissions=True)
		self._set_job_modified_days_ago(job.name, 10)

		cleanup_old_audio_jobs()
		job.reload()
		self.assertFalse(job.zip_file)

	def test_cleanup_deletes_manual_transcripts_only_when_enabled(self):
		self._enable_cleanup(delete_manual_transcripts_after_retention=1)
		job = self._create_job()
		job.manual_transcript_json_file = self._create_attached_file(job, "manual.json", b"{}")
		job.save(ignore_permissions=True)
		self._set_job_modified_days_ago(job.name, 10)

		cleanup_old_audio_jobs()
		job.reload()
		self.assertFalse(job.manual_transcript_json_file)

	def test_cleanup_deletes_old_audit_logs_when_retention_configured(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.audit_log_retention_days = 7
		settings.save(ignore_permissions=True)

		log_audit("Admin View", message="old entry")
		old_name = frappe.get_last_doc("Audio Stem Audit Log").name
		frappe.db.sql(
			"UPDATE `tabAudio Stem Audit Log` SET created_at = %s WHERE name = %s",
			(add_days(now_datetime(), -30), old_name),
		)
		frappe.db.commit()

		deleted = cleanup_old_audit_logs(settings)
		self.assertGreaterEqual(deleted, 1)
		self.assertFalse(frappe.db.exists("Audio Stem Audit Log", old_name))

	def test_cleanup_preserves_recent_audit_logs(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.audit_log_retention_days = 30
		settings.save(ignore_permissions=True)

		log_audit("Admin View", message="recent entry")
		recent_name = frappe.get_last_doc("Audio Stem Audit Log").name
		deleted = cleanup_old_audit_logs(settings)
		self.assertGreaterEqual(deleted, 0)
		self.assertTrue(frappe.db.exists("Audio Stem Audit Log", recent_name))

	def test_cleanup_is_idempotent(self):
		self._enable_cleanup(delete_transcripts_after_retention=1)
		job = self._create_job()
		job.transcript_json_file = self._create_attached_file(job, "t.json", b"{}")
		job.save(ignore_permissions=True)
		self._set_job_modified_days_ago(job.name, 10)

		cleanup_old_audio_jobs()
		cleanup_old_audio_jobs()
		job.reload()
		self.assertFalse(job.transcript_json_file)
		self.assertTrue(job.cleanup_notes)


class TestGate10Phase2PipelineCancellation(AudioStemTestCase):
	def test_cancel_before_transcription_call_sets_cancelled(self):
		job = self._create_job()
		job.status = "Completed"
		job.transcription_status = "Queued"
		job.cancellation_requested = 1
		job.save(ignore_permissions=True)

		process_transcription(job.name)
		job.reload()
		self.assertEqual(job.transcription_status, "Cancelled")
		self.assertFalse(job.transcript_json_file)

	def test_cancel_after_transcription_provider_does_not_save_assets(self):
		job = self._create_job()
		job.status = "Completed"
		job.vocal_output_url = "https://example.com/vocal.mp3"
		job.save(ignore_permissions=True)

		def _transcribe_and_cancel(_path, language=None):
			active = frappe.get_doc("Audio Separation Job", job.name)
			active.cancellation_requested = 1
			active.save(ignore_permissions=True)
			frappe.db.commit()
			return {"text": "new lyrics", "segments": []}

		with patch(
			"audio_stem.workers.transcription_worker.resolve_transcription_source_path",
			return_value="/tmp/fake.mp3",
		), patch(
			"audio_stem.workers.transcription_worker.prepare_audio_for_whisper",
			return_value=("/tmp/fake.mp3", False),
		), patch(
			"audio_stem.workers.transcription_worker.transcribe_with_whisper",
			side_effect=_transcribe_and_cancel,
		):
			process_transcription(job.name)

		job.reload()
		self.assertEqual(job.transcription_status, "Cancelled")
		self.assertFalse(job.transcript_json_file)

	def test_cancel_before_karaoke_ass_sets_cancelled(self):
		job = self._create_job()
		job.status = "Completed"
		job.transcription_status = "Completed"
		job.karaoke_status = "Queued"
		job.karaoke_ass_file = "/private/files/old.ass"
		job.cancellation_requested = 1
		job.save(ignore_permissions=True)

		process_karaoke_render(job.name)
		job.reload()
		self.assertEqual(job.karaoke_status, "Cancelled")
		self.assertEqual(job.karaoke_ass_file, "/private/files/old.ass")

	def test_cancel_after_mp4_render_does_not_overwrite_old_mp4(self):
		job = self._create_job()
		job.status = "Completed"
		job.transcription_status = "Completed"
		job.karaoke_video_file = "/private/files/old.mp4"
		job.karaoke_ass_file = "/private/files/old.ass"
		job.save(ignore_permissions=True)

		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_enabled = 1
		settings.karaoke_ass_enabled = 1
		settings.karaoke_video_render_enabled = 1
		settings.save(ignore_permissions=True)

		def _render_and_cancel(*args, **kwargs):
			active = frappe.get_doc("Audio Separation Job", job.name)
			active.cancellation_requested = 1
			active.save(ignore_permissions=True)
			frappe.db.commit()
			return "/private/files/new.mp4"

		with patch(
			"audio_stem.workers.karaoke_worker.load_karaoke_transcript_data",
			return_value={"words": [], "segments": []},
		), patch(
			"audio_stem.workers.karaoke_worker.build_karaoke_words_json",
			return_value={"words": []},
		), patch(
			"audio_stem.workers.karaoke_worker.write_karaoke_json",
		), patch(
			"audio_stem.workers.karaoke_worker.build_karaoke_ass_with_engine",
			return_value="/private/files/new.ass",
		), patch(
			"audio_stem.workers.karaoke_worker.render_karaoke_video_with_engine",
			side_effect=_render_and_cancel,
		):
			process_karaoke_render(job.name)

		job.reload()
		self.assertEqual(job.karaoke_status, "Cancelled")
		self.assertEqual(job.karaoke_video_file, "/private/files/old.mp4")

	def test_cancel_active_transcription_via_api(self):
		job = self._create_job()
		job.status = "Completed"
		job.transcription_status = "Processing"
		job.save(ignore_permissions=True)

		result = cancel_job(job.name)
		self.assertTrue(result.get("cancellation_requested"))
		job.reload()
		self.assertEqual(job.cancellation_requested, 1)
		self.assertEqual(job.status, "Completed")


class TestGate10Phase2CreditVisibility(AudioStemTestCase):
	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	def test_job_detail_includes_reconciliation_flag_safely(self, _available_mock):
		settings = frappe.get_single("Audio Separation Settings")
		settings.credit_management_enabled = 1
		settings.save(ignore_permissions=True)

		job = self._create_job()
		job.status = "Completed"
		job.credit_status = "Reconciliation Required"
		job.credit_error = "Unable to verify credit balance."
		job.credit_reservation = "RES-PHASE2"
		job.save(ignore_permissions=True)

		payload = get_job_detail(job.name)
		self.assertTrue(payload["reconciliation_required"])
		self.assertEqual(payload["credit_error"], job.credit_error)
		self.assertNotIn("traceback", (payload["credit_error"] or "").lower())
		self.assertNotIn("api_key", (payload["credit_error"] or "").lower())

	def test_normal_user_cannot_access_reconciliation_admin_api(self):
		email = f"phase2-user-{frappe.generate_hash(length=6)}@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "phase2",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
		frappe.set_user(email)
		with self.assertRaises(frappe.PermissionError):
			admin_api.get_credit_reconciliation_issues()

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.consume_reserved_credits")
	def test_system_manager_can_retry_reconciliation(self, consume_mock, _available_mock):
		settings = frappe.get_single("Audio Separation Settings")
		settings.credit_management_enabled = 1
		settings.save(ignore_permissions=True)

		job = self._create_job()
		job.status = "Completed"
		job.credit_status = "Reconciliation Required"
		job.credit_reservation = "RES-ADMIN"
		job.provider_cost_usd = 0.03
		job.reserved_amount = 0.03
		job.save(ignore_permissions=True)

		consume_mock.return_value = {"consumed_amount": 0.03, "status": "Consumed"}
		result = admin_api.retry_credit_reconciliation(job.name)
		self.assertEqual(result["credit_status"], "Consumed")
		consume_mock.assert_called_once()


class TestGate10Phase2KaraokeOutputRepair(AudioStemTestCase):
	def test_get_job_detail_restores_missing_karaoke_video_link(self):
		job = self._create_job()
		job.status = "Completed"
		job.transcription_status = "Completed"
		job.karaoke_status = "Completed"
		job.karaoke_ass_file = "/private/files/test.ass"
		job.karaoke_video_file = None
		job.save(ignore_permissions=True)

		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{job.name}-karaoke.mp4",
				"attached_to_doctype": job.doctype,
				"attached_to_name": job.name,
				"attached_to_field": "karaoke_video_file",
				"is_private": 1,
				"content": b"fake-mp4",
			}
		)
		file_doc.save(ignore_permissions=True)

		payload = get_job_detail(job.name)
		self.assertEqual(payload["karaoke_video_file"], file_doc.file_url)
		self.assertTrue(payload["has_karaoke_video"])

	def test_karaoke_worker_keeps_video_after_post_render_cancel_check(self):
		job = self._create_job()
		job.status = "Completed"
		job.transcription_status = "Completed"
		job.save(ignore_permissions=True)

		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_enabled = 1
		settings.karaoke_ass_enabled = 1
		settings.karaoke_video_render_enabled = 1
		settings.save(ignore_permissions=True)

		video_url = "/private/files/test-karaoke.mp4"

		with patch(
			"audio_stem.workers.karaoke_worker.load_karaoke_transcript_data",
			return_value={"words": [], "segments": []},
		), patch(
			"audio_stem.workers.karaoke_worker.build_karaoke_words_json",
			return_value={"words": []},
		), patch(
			"audio_stem.workers.karaoke_worker.write_karaoke_json",
		), patch(
			"audio_stem.workers.karaoke_worker.build_karaoke_ass_with_engine",
			return_value="/private/files/test.ass",
		), patch(
			"audio_stem.workers.karaoke_worker.render_karaoke_video_with_engine",
			side_effect=lambda job, **kwargs: setattr(job, "karaoke_video_file", video_url) or video_url,
		), patch(
			"audio_stem.workers.karaoke_worker.cancellation_requested_for_job",
			return_value=False,
		):
			process_karaoke_render(job.name)

		job.reload()
		self.assertEqual(job.karaoke_status, "Completed")
		self.assertEqual(job.karaoke_video_file, video_url)
