# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from audio_stem.api.separation import get_my_credit_balance, start_separation
from audio_stem.integrations.credit_management_client import (
	consume_job_reservation,
	release_job_reservation,
	reserve_job_credits,
)
from audio_stem.integrations.wavespeed_client import SeparationResult
from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.limits import calculate_provider_cost
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


class TestAudioSeparationMilestone4(FrappeTestCase):
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
			"reservation": "RES-TEST-001",
			"reserved_amount": amount,
			"credit_type": "AUDIO_STEM",
			"available_balance": 0,
		}

	def test_credit_disabled_keeps_existing_behavior(self):
		job = self._create_draft_job()
		with patch("audio_stem.api.separation.frappe.enqueue") as enqueue_mock:
			result = start_separation(job.name)

		job.reload()
		self.assertEqual(result["status"], "Queued")
		self.assertEqual(job.credit_status, "Not Required")
		self.assertFalse(job.credit_reservation)
		enqueue_mock.assert_called_once()

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.reserve_credits")
	@patch(f"{CREDIT_API}.get_balance")
	def test_credit_enabled_blocks_start_when_balance_insufficient(
		self, get_balance_mock, reserve_mock, _available_mock
	):
		self._enable_credit_management()
		job = self._create_draft_job(duration_seconds=30)
		get_balance_mock.return_value = self._mock_balance(available_balance=0.001)

		with self.assertRaises(frappe.ValidationError):
			start_separation(job.name)

		reserve_mock.assert_not_called()

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.reserve_credits")
	@patch(f"{CREDIT_API}.get_balance")
	def test_credit_enabled_reserves_before_enqueue(self, get_balance_mock, reserve_mock, _available_mock):
		self._enable_credit_management()
		job = self._create_draft_job(duration_seconds=30)
		expected_cost = calculate_provider_cost(30)
		get_balance_mock.return_value = self._mock_balance(available_balance=expected_cost + 10)
		reserve_mock.return_value = self._mock_reserve(expected_cost)

		with patch("audio_stem.api.separation.frappe.enqueue") as enqueue_mock:
			start_separation(job.name)

		job.reload()
		self.assertEqual(job.credit_status, "Reserved")
		self.assertEqual(job.credit_reservation, "RES-TEST-001")
		self.assertEqual(job.reserved_amount, expected_cost)
		self.assertEqual(job.credit_type, "AUDIO_STEM")
		reserve_mock.assert_called_once()
		enqueue_mock.assert_called_once()

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.reserve_credits")
	@patch(f"{CREDIT_API}.get_balance")
	def test_repeated_start_does_not_create_duplicate_reservation(
		self, get_balance_mock, reserve_mock, _available_mock
	):
		self._enable_credit_management()
		job = self._create_draft_job(duration_seconds=30)
		expected_cost = calculate_provider_cost(30)
		get_balance_mock.return_value = self._mock_balance(available_balance=expected_cost + 10)
		reserve_mock.return_value = self._mock_reserve(expected_cost)

		with patch("audio_stem.api.separation.frappe.enqueue") as enqueue_mock:
			first = start_separation(job.name)
			second = start_separation(job.name)

		self.assertFalse(first.get("already_active"))
		self.assertTrue(second.get("already_active"))
		reserve_mock.assert_called_once()
		enqueue_mock.assert_called_once()

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.consume_reserved_credits")
	@patch(f"{CREDIT_API}.reserve_credits")
	@patch(f"{CREDIT_API}.get_balance")
	def test_successful_worker_completion_consumes_reserved_credits(
		self, get_balance_mock, reserve_mock, consume_mock, _available_mock
	):
		self._enable_credit_management()
		job = self._create_draft_job(duration_seconds=30)
		expected_cost = calculate_provider_cost(30)
		get_balance_mock.return_value = self._mock_balance(available_balance=expected_cost + 10)
		reserve_mock.return_value = self._mock_reserve(expected_cost)
		consume_mock.return_value = {"consumed_amount": expected_cost, "status": "Consumed"}

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

		job.reload()
		self.assertEqual(job.status, "Completed")
		self.assertEqual(job.credit_status, "Consumed")
		self.assertEqual(job.consumed_amount, expected_cost)
		consume_mock.assert_called_once()

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.release_reservation")
	@patch(f"{CREDIT_API}.reserve_credits")
	@patch(f"{CREDIT_API}.get_balance")
	def test_failed_worker_releases_reservation(
		self, get_balance_mock, reserve_mock, release_mock, _available_mock
	):
		self._enable_credit_management()
		job = self._create_draft_job(duration_seconds=30)
		expected_cost = calculate_provider_cost(30)
		get_balance_mock.return_value = self._mock_balance(available_balance=expected_cost + 10)
		reserve_mock.return_value = self._mock_reserve(expected_cost)
		release_mock.return_value = {"status": "Released"}

		with patch("audio_stem.api.separation.frappe.enqueue"):
			start_separation(job.name)

		with patch(
			"audio_stem.workers.separation_worker.isolate_vocal_and_instrumental",
			side_effect=RuntimeError("provider failed"),
		):
			process_audio_separation(job.name)

		job.reload()
		self.assertEqual(job.status, "Failed")
		self.assertEqual(job.credit_status, "Released")
		release_mock.assert_called_once()

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.consume_reserved_credits")
	@patch(f"{CREDIT_API}.reserve_credits")
	@patch(f"{CREDIT_API}.get_balance")
	def test_consume_failure_after_success_keeps_job_completed(
		self, get_balance_mock, reserve_mock, consume_mock, _available_mock
	):
		self._enable_credit_management()
		job = self._create_draft_job(duration_seconds=30)
		expected_cost = calculate_provider_cost(30)
		get_balance_mock.return_value = self._mock_balance(available_balance=expected_cost + 10)
		reserve_mock.return_value = self._mock_reserve(expected_cost)
		consume_mock.side_effect = RuntimeError("consume failed api_key=secret")

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
		self.assertEqual(job.credit_status, "Failed")
		self.assertNotIn("secret", job.credit_error or "")
		self.assertNotIn("api_key", (job.credit_error or "").lower())
		self.assertEqual(
			job.credit_error,
			safe_error_message(RuntimeError("consume failed api_key=secret")),
		)

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.release_reservation")
	@patch(f"{CREDIT_API}.reserve_credits")
	@patch(f"{CREDIT_API}.get_balance")
	def test_release_failure_saves_safe_credit_error(
		self, get_balance_mock, reserve_mock, release_mock, _available_mock
	):
		self._enable_credit_management()
		job = self._create_draft_job(duration_seconds=30)
		expected_cost = calculate_provider_cost(30)
		get_balance_mock.return_value = self._mock_balance(available_balance=expected_cost + 10)
		reserve_mock.return_value = self._mock_reserve(expected_cost)
		release_mock.side_effect = RuntimeError("release failed api_key=secret")

		with patch("audio_stem.api.separation.frappe.enqueue"):
			start_separation(job.name)

		with patch(
			"audio_stem.workers.separation_worker.isolate_vocal_and_instrumental",
			side_effect=RuntimeError("provider failed"),
		):
			process_audio_separation(job.name)

		job.reload()
		self.assertEqual(job.status, "Failed")
		self.assertEqual(job.credit_status, "Failed")
		self.assertNotIn("secret", job.credit_error or "")
		self.assertNotIn("api_key", (job.credit_error or "").lower())
		self.assertEqual(
			job.credit_error,
			safe_error_message(RuntimeError("release failed api_key=secret")),
		)

	def test_missing_credit_management_app_gives_safe_configuration_error(self):
		self._enable_credit_management()

		with patch(f"{CLIENT}.credit_management_available", return_value=False):
			balance = get_my_credit_balance()

		self.assertTrue(balance.get("enabled"))
		self.assertIn("not installed", (balance.get("error") or "").lower())

		job = self._create_draft_job()
		with patch(f"{CLIENT}.credit_management_available", return_value=False):
			with self.assertRaises(frappe.ValidationError):
				start_separation(job.name)

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	def test_get_my_credit_balance_when_disabled(self, _available_mock):
		result = get_my_credit_balance()
		self.assertFalse(result.get("enabled"))

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.get_balance")
	def test_get_my_credit_balance_returns_balances(self, get_balance_mock, _available_mock):
		self._enable_credit_management()
		get_balance_mock.return_value = self._mock_balance(available_balance=42)

		result = get_my_credit_balance()
		self.assertTrue(result.get("enabled"))
		self.assertEqual(result.get("credit_type"), "AUDIO_STEM")
		self.assertEqual(result.get("available_balance"), 42)

	def test_audio_stem_does_not_reference_credit_ledger_doctypes(self):
		python_files = [
			path
			for path in APP_ROOT.rglob("*.py")
			if "node_modules" not in path.parts and "tests" not in path.parts
		]
		for path in python_files:
			content = path.read_text(encoding="utf-8")
			for pattern in FORBIDDEN_CREDIT_PATTERNS:
				self.assertNotIn(
					pattern,
					content,
					msg=f"Forbidden credit reference {pattern!r} found in {path.relative_to(APP_ROOT)}",
				)

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.reserve_credits")
	@patch(f"{CREDIT_API}.get_balance")
	def test_reserve_uses_stable_idempotency_key(self, get_balance_mock, reserve_mock, _available_mock):
		self._enable_credit_management()
		job = self._create_draft_job(duration_seconds=30)
		expected_cost = calculate_provider_cost(30)
		get_balance_mock.return_value = self._mock_balance(available_balance=expected_cost + 10)
		reserve_mock.return_value = self._mock_reserve(expected_cost)

		with patch("audio_stem.api.separation.frappe.enqueue"):
			start_separation(job.name)

		_, kwargs = reserve_mock.call_args
		self.assertEqual(kwargs["idempotency_key"], f"audio_stem:{job.name}:reserve")
		self.assertEqual(kwargs["source_app"], "audio_stem")

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.consume_reserved_credits")
	def test_consume_uses_stable_idempotency_key(self, consume_mock, _available_mock):
		job = self._create_draft_job(duration_seconds=30)
		job.credit_reservation = "RES-TEST-001"
		job.credit_status = "Reserved"
		job.provider_cost_usd = calculate_provider_cost(30)
		consume_mock.return_value = {"consumed_amount": job.provider_cost_usd}

		consume_job_reservation(job)

		_, kwargs = consume_mock.call_args
		self.assertEqual(kwargs["idempotency_key"], f"audio_stem:{job.name}:consume")
		self.assertEqual(kwargs["source_app"], "audio_stem")

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.release_reservation")
	def test_release_uses_stable_idempotency_key(self, release_mock, _available_mock):
		job = self._create_draft_job(duration_seconds=30)
		job.credit_reservation = "RES-TEST-001"
		job.credit_status = "Reserved"
		release_mock.return_value = {"status": "Released"}

		release_job_reservation(job, reason="test")

		_, kwargs = release_mock.call_args
		self.assertEqual(kwargs["idempotency_key"], f"audio_stem:{job.name}:release")

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.consume_reserved_credits")
	def test_consume_is_idempotent_when_already_consumed(self, consume_mock, _available_mock):
		job = self._create_draft_job(duration_seconds=30)
		job.credit_reservation = "RES-TEST-001"
		job.credit_status = "Consumed"
		job.consumed_amount = 0.03

		result = consume_job_reservation(job)

		consume_mock.assert_not_called()
		self.assertTrue(result.get("idempotent_replay"))

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.release_reservation")
	def test_release_is_idempotent_when_already_released(self, release_mock, _available_mock):
		job = self._create_draft_job(duration_seconds=30)
		job.credit_reservation = "RES-TEST-001"
		job.credit_status = "Released"

		result = release_job_reservation(job, reason="test")

		release_mock.assert_not_called()
		self.assertTrue(result.get("idempotent_replay"))

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.reserve_credits")
	@patch(f"{CREDIT_API}.get_balance")
	def test_draft_to_queued_sets_credit_status_reserved(
		self, get_balance_mock, reserve_mock, _available_mock
	):
		self._enable_credit_management()
		job = self._create_draft_job(duration_seconds=30)
		expected_cost = calculate_provider_cost(30)
		get_balance_mock.return_value = self._mock_balance(available_balance=expected_cost + 10)
		reserve_mock.return_value = self._mock_reserve(expected_cost)

		self.assertEqual(job.status, "Draft")
		job.credit_status = "Pending"
		job.save(ignore_permissions=True)

		with patch("audio_stem.api.separation.frappe.enqueue"):
			start_separation(job.name)

		job.reload()
		self.assertEqual(job.status, "Queued")
		self.assertEqual(job.credit_status, "Reserved")

	@patch(f"{CLIENT}.credit_management_available", return_value=True)
	@patch(f"{CREDIT_API}.release_reservation")
	@patch(f"{CREDIT_API}.reserve_credits")
	@patch(f"{CREDIT_API}.get_balance")
	def test_enqueue_failure_releases_credits_and_returns_job_to_draft(
		self, get_balance_mock, reserve_mock, release_mock, _available_mock
	):
		self._enable_credit_management()
		job = self._create_draft_job(duration_seconds=30)
		expected_cost = calculate_provider_cost(30)
		get_balance_mock.return_value = self._mock_balance(available_balance=expected_cost + 10)
		reserve_mock.return_value = self._mock_reserve(expected_cost)
		release_mock.return_value = {"status": "Released"}

		with patch("audio_stem.api.separation.frappe.enqueue", side_effect=RuntimeError("queue down")):
			with self.assertRaises(RuntimeError):
				start_separation(job.name)

		job.reload()
		self.assertEqual(job.status, "Draft")
		self.assertEqual(job.credit_status, "Released")
		release_mock.assert_called_once()
		reserve_mock.assert_called_once()
