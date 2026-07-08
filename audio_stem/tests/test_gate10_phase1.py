# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from audio_stem.api import admin as admin_api
from audio_stem.api.separation import create_job_from_file, start_karaoke_render, start_transcription
from audio_stem.integrations.wavespeed_client import SeparationResult
from audio_stem.tests.base import AudioStemTestCase
from audio_stem.utils.credit_reconciliation import (
	CREDIT_RECONCILIATION_STATUS,
	get_credit_reconciliation_issues,
	retry_job_credit_consume,
)
from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.ffmpeg_media import _run_ffmpeg, get_ffmpeg_timeout_seconds
from audio_stem.utils.limits import calculate_provider_cost
from audio_stem.utils.transcription_karaoke_controls import can_start_karaoke, can_start_transcription
from audio_stem.workers.karaoke_worker import process_karaoke_render
from audio_stem.workers.separation_worker import process_audio_separation

CREDIT_API = "credit_management.api"
CLIENT = "audio_stem.integrations.credit_management_client"
APP_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_CREDIT_PATTERNS = (
	"Credit Account",
	"Credit Ledger Entry",
	"Credit Ledger",
	"tabCredit Account",
	"tabCredit Ledger Entry",
)


class TestGate10Phase1CreditReconciliation(AudioStemTestCase):
	def _enable_credit_management(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.credit_management_enabled = 1
		settings.save(ignore_permissions=True)

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

	def _create_draft_job(self, duration_seconds=30):
		job = frappe.get_doc(
			{
				"doctype": "Audio Separation Job",
				"user": frappe.session.user,
				"status": "Draft",
				"original_file": self._create_file().file_url,
				"duration_seconds": duration_seconds,
			}
		)
		job.insert(ignore_permissions=True)
		return job

	def _mock_balance(self, available_balance=100):
		return {
			"current_balance": available_balance,
			"reserved_balance": 0,
			"available_balance": available_balance,
		}

	def _mock_reserve(self, amount):
		return {
			"reservation": "RES-GATE10-001",
			"reserved_amount": amount,
			"credit_type": "AUDIO_STEM",
			"available_balance": 0,
		}

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.consume_reserved_credits")
	@patch(f"{CREDIT_API}.reserve_credits")
	@patch(f"{CREDIT_API}.get_balance")
	def test_consume_failure_marks_reconciliation_required_and_preserves_outputs(
		self, get_balance_mock, reserve_mock, consume_mock, _available_mock
	):
		self._enable_credit_management()
		job = self._create_draft_job(duration_seconds=30)
		expected_cost = calculate_provider_cost(30)
		get_balance_mock.return_value = self._mock_balance(available_balance=expected_cost + 10)
		reserve_mock.return_value = self._mock_reserve(expected_cost)
		consume_mock.side_effect = RuntimeError("consume failed api_key=secret")

		from audio_stem.api.separation import start_separation

		with patch("audio_stem.api.separation.frappe.enqueue"):
			start_separation(job.name)

		vocal_url = "https://example.com/vocal.mp3"
		instrumental_url = "https://example.com/instrumental.mp3"
		with patch(
			"audio_stem.workers.separation_worker.isolate_vocal_and_instrumental",
			return_value=SeparationResult(vocal_url=vocal_url, instrumental_url=instrumental_url),
		):
			process_audio_separation(job.name)

		job.reload()
		self.assertEqual(job.status, "Completed")
		self.assertEqual(job.vocal_output_url, vocal_url)
		self.assertEqual(job.credit_status, CREDIT_RECONCILIATION_STATUS)
		self.assertEqual(
			job.credit_error,
			safe_error_message(RuntimeError("consume failed api_key=secret")),
		)

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.consume_reserved_credits")
	@patch(f"{CREDIT_API}.reserve_credits")
	@patch(f"{CREDIT_API}.get_balance")
	def test_reconciliation_endpoint_lists_affected_job(
		self, get_balance_mock, reserve_mock, consume_mock, _available_mock
	):
		self._enable_credit_management()
		job = self._create_draft_job(duration_seconds=30)
		expected_cost = calculate_provider_cost(30)
		get_balance_mock.return_value = self._mock_balance(available_balance=expected_cost + 10)
		reserve_mock.return_value = self._mock_reserve(expected_cost)
		consume_mock.side_effect = RuntimeError("consume failed")

		from audio_stem.api.separation import start_separation

		with patch("audio_stem.api.separation.frappe.enqueue"):
			start_separation(job.name)

		with patch(
			"audio_stem.workers.separation_worker.isolate_vocal_and_instrumental",
			return_value=SeparationResult(
				vocal_url="https://example.com/vocal.mp3",
				instrumental_url="https://example.com/instrumental.mp3",
			),
		):
			process_audio_separation(job.name)

		issues = get_credit_reconciliation_issues()
		names = {row["name"] for row in issues}
		self.assertIn(job.name, names)

		admin_issues = admin_api.get_credit_reconciliation_issues()
		self.assertIn(job.name, {row["name"] for row in admin_issues})

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.consume_reserved_credits")
	def test_retry_reconciliation_consumes_without_duplicate_calls(self, consume_mock, _available_mock):
		self._enable_credit_management()
		job = self._create_draft_job(duration_seconds=30)
		job.status = "Completed"
		job.credit_reservation = "RES-GATE10-002"
		job.credit_status = CREDIT_RECONCILIATION_STATUS
		job.provider_cost_usd = calculate_provider_cost(30)
		job.reserved_amount = job.provider_cost_usd
		job.save(ignore_permissions=True)

		consume_mock.return_value = {"consumed_amount": job.provider_cost_usd, "status": "Consumed"}

		retry_job_credit_consume(job)
		job.reload()
		self.assertEqual(job.credit_status, "Consumed")
		consume_mock.assert_called_once()

		consume_mock.reset_mock()
		result = retry_job_credit_consume(job)
		consume_mock.assert_not_called()
		self.assertTrue(result.get("idempotent_replay"))

	def test_retry_reconciliation_does_not_mutate_credit_tables_directly(self):
		source = (APP_ROOT / "integrations" / "credit_management_client.py").read_text(encoding="utf-8")
		for pattern in FORBIDDEN_CREDIT_PATTERNS:
			self.assertNotIn(pattern, source)


class TestGate10Phase1FfmpegTimeout(AudioStemTestCase):
	def test_timeout_setting_default_exists(self):
		settings = frappe.get_single("Audio Separation Settings")
		self.assertEqual(cint_or_zero(settings.karaoke_ffmpeg_timeout_seconds), 1800)
		self.assertEqual(get_ffmpeg_timeout_seconds(), 1800)

	def test_ffmpeg_timeout_is_passed_to_subprocess(self):
		with patch("audio_stem.utils.ffmpeg_media.subprocess.run") as run_mock:
			run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
			_run_ffmpeg(["-i", "input.mp3", "output.mp3"], timeout_seconds=42)
		_, kwargs = run_mock.call_args
		self.assertEqual(kwargs["timeout"], 42)

	def test_timeout_raises_safe_error(self):
		with patch(
			"audio_stem.utils.ffmpeg_media.subprocess.run",
			side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1),
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				_run_ffmpeg(["-i", "input.mp3", "output.mp3"], timeout_seconds=1)
		self.assertIn("timed out", str(ctx.exception).lower())

	def test_ass_preserved_when_mp4_render_times_out(self):
		job = self._create_job()
		job.status = "Completed"
		job.transcription_status = "Completed"
		job.transcript_json_file = "/private/files/transcript.json"
		job.karaoke_ass_file = "/private/files/existing.ass"
		job.save(ignore_permissions=True)

		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_video_render_enabled = 1
		settings.karaoke_enabled = 1
		settings.karaoke_ass_enabled = 1
		settings.save(ignore_permissions=True)

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
			side_effect=frappe.ValidationError("Media processing timed out. Please try again later."),
		):
			process_karaoke_render(job.name)

		job.reload()
		self.assertEqual(job.karaoke_status, "Completed")
		self.assertIn("Video render failed", job.karaoke_error or "")


def cint_or_zero(value):
	from frappe.utils import cint

	return cint(value) or 0


class TestGate10Phase1CancelAfterProvider(AudioStemTestCase):
	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.release_reservation")
	@patch(f"{CREDIT_API}.reserve_credits")
	@patch(f"{CREDIT_API}.get_balance")
	def test_cancel_after_provider_return_cancels_and_releases(
		self, get_balance_mock, reserve_mock, release_mock, _available_mock
	):
		settings = frappe.get_single("Audio Separation Settings")
		settings.credit_management_enabled = 1
		settings.save(ignore_permissions=True)

		job = self._create_job()
		expected_cost = calculate_provider_cost(job.duration_seconds)
		get_balance_mock.return_value = {
			"current_balance": 100,
			"reserved_balance": 0,
			"available_balance": 100,
		}
		reserve_mock.return_value = {
			"reservation": "RES-CANCEL-001",
			"reserved_amount": expected_cost,
		}
		release_mock.return_value = {"status": "Released"}

		from audio_stem.api.separation import start_separation

		with patch("audio_stem.api.separation.frappe.enqueue"):
			start_separation(job.name)

		def _isolate_and_request_cancel(_path):
			active = frappe.get_doc("Audio Separation Job", job.name)
			active.cancellation_requested = 1
			active.cancel_reason = "User requested cancel"
			active.save(ignore_permissions=True)
			frappe.db.commit()
			return SeparationResult(
				vocal_url="https://example.com/vocal.mp3",
				instrumental_url="https://example.com/instrumental.mp3",
			)

		with patch(
			"audio_stem.workers.separation_worker.isolate_vocal_and_instrumental",
			side_effect=_isolate_and_request_cancel,
		):
			process_audio_separation(job.name)

		job.reload()
		self.assertEqual(job.status, "Cancelled")
		self.assertNotEqual(job.vocal_output_url, "https://example.com/vocal.mp3")
		self.assertEqual(job.credit_status, "Released")
		release_mock.assert_called_once()

		audit = frappe.get_all(
			"Audio Stem Audit Log",
			filters={"reference_name": job.name, "action": "Cancel Job"},
			fields=["message"],
			order_by="creation desc",
		)
		messages = " ".join((row.message or "").lower() for row in audit)
		self.assertIn("provider returned", messages)


class TestGate10Phase1FileOwnership(AudioStemTestCase):
	def setUp(self):
		super().setUp()
		self.user_a = f"gate10-owner-a-{frappe.generate_hash(length=6)}@example.com"
		self.user_b = f"gate10-owner-b-{frappe.generate_hash(length=6)}@example.com"
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
			tmp.write(b"fake-audio")
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

	def test_owner_can_create_job_from_own_file(self):
		file_url = self._upload_file_as(self.user_a)
		frappe.set_user(self.user_a)
		result = create_job_from_file(file_url)
		self.assertTrue(result["name"])

	def test_other_user_cannot_create_job_from_foreign_file(self):
		file_url = self._upload_file_as(self.user_a)
		frappe.set_user(self.user_b)
		with self.assertRaises(frappe.PermissionError):
			create_job_from_file(file_url)

	def test_guest_blocked_from_create_job(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			create_job_from_file("/private/files/test.mp3")

	def test_system_manager_can_use_foreign_file(self):
		file_url = self._upload_file_as(self.user_a)
		frappe.set_user("Administrator")
		result = create_job_from_file(file_url)
		self.assertTrue(result["name"])


class TestGate10Phase1CancelledRestart(AudioStemTestCase):
	def setUp(self):
		super().setUp()
		settings = frappe.get_single("Audio Separation Settings")
		settings.openai_enabled = 1
		settings.openai_api_key = "test-openai-key"
		settings.karaoke_enabled = 1
		settings.karaoke_ass_enabled = 1
		settings.save(ignore_permissions=True)

	def _completed_separation_job(self):
		job = self._create_job()
		job.status = "Completed"
		job.vocal_file = job.original_file
		job.save(ignore_permissions=True)
		return job

	def test_cancelled_transcription_can_restart(self):
		job = self._completed_separation_job()
		job.transcription_status = "Cancelled"
		job.transcription_error = "Cancelled by user"
		job.save(ignore_permissions=True)

		can_start, reason = can_start_transcription(job)
		self.assertTrue(can_start, reason)

		with patch("audio_stem.api.separation.frappe.enqueue"):
			result = start_transcription(job.name)

		job.reload()
		self.assertEqual(result["transcription_status"], "Queued")
		self.assertIsNone(job.transcription_error)

	def test_cancelled_karaoke_can_restart(self):
		job = self._completed_separation_job()
		job.transcription_status = "Completed"
		job.karaoke_status = "Cancelled"
		job.karaoke_error = "Cancelled by user"
		job.karaoke_ass_file = "/private/files/old.ass"
		job.save(ignore_permissions=True)

		can_start, reason = can_start_karaoke(job)
		self.assertTrue(can_start, reason)

		with patch("audio_stem.api.separation.frappe.enqueue"):
			result = start_karaoke_render(job.name)

		job.reload()
		self.assertEqual(result["karaoke_status"], "Queued")
		self.assertIsNone(job.karaoke_error)
		self.assertEqual(job.karaoke_ass_file, "/private/files/old.ass")

	def test_active_transcription_cannot_duplicate(self):
		job = self._completed_separation_job()
		job.transcription_status = "Processing"
		job.save(ignore_permissions=True)

		can_start, _ = can_start_transcription(job)
		self.assertFalse(can_start)

	def test_active_karaoke_cannot_duplicate(self):
		job = self._completed_separation_job()
		job.transcription_status = "Completed"
		job.karaoke_status = "Rendering"
		job.save(ignore_permissions=True)

		can_start, _ = can_start_karaoke(job)
		self.assertFalse(can_start)
