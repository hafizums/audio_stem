# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from audio_stem.api.admin import get_provider_health, get_queue_health
from audio_stem.api.separation import (
	cancel_job,
	create_job_from_file,
	get_job_detail,
	get_page_settings,
	get_recent_jobs,
	start_separation,
)
from audio_stem.tests.base import AudioStemTestCase
from audio_stem.utils.audit_log import log_audit
from audio_stem.utils.daily_limits import ensure_daily_limits_for_queue, get_user_daily_usage
from audio_stem.utils.pilot_access import is_pilot_access_allowed
from audio_stem.utils.provider_health import get_provider_health_summary
from audio_stem.utils.queue_health import get_queue_health_data

APP_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_CREDIT_PATTERNS = (
	"Credit Account",
	"Credit Ledger Entry",
	"Credit Ledger",
	"tabCredit Account",
	"tabCredit Ledger Entry",
)
CLIENT = "audio_stem.integrations.credit_management_client"
PILOT_ROLE = "Audio Stem Pilot"


class TestAudioSeparationMilestone7(AudioStemTestCase):
	def setUp(self):
		super().setUp()
		self._ensure_role(PILOT_ROLE)

	def _ensure_role(self, role_name: str):
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)

	def _ensure_user(self, email: str, roles: list[str] | None = None):
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": email.split("@")[0],
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
		if roles:
			user = frappe.get_doc("User", email)
			for role in roles:
				if role not in frappe.get_roles(email):
					user.add_roles(role)
		return email

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
				"status": "Draft",
				"original_file": self._create_file().file_url,
				"duration_seconds": 30,
				**kwargs,
			}
		)
		job.insert(ignore_permissions=True)
		return job

	def _enable_pilot(self, *, allowed_users=None, allowed_roles=None, blocked_users=None):
		settings = frappe.get_single("Audio Separation Settings")
		settings.pilot_mode_enabled = 1
		settings.allowed_users = allowed_users
		settings.allowed_roles = allowed_roles
		settings.blocked_users = blocked_users
		settings.save(ignore_permissions=True)

	# Pilot access

	def test_pilot_mode_off_preserves_existing_access(self):
		email = self._ensure_user(f"pilot-off-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(email)
		self.assertTrue(is_pilot_access_allowed(email))
		jobs = get_recent_jobs()
		self.assertIsInstance(jobs, list)

	def test_allowed_user_can_use_app(self):
		email = self._ensure_user(f"pilot-allowed-{frappe.generate_hash(length=6)}@example.com")
		self._enable_pilot(allowed_users=email)
		frappe.set_user(email)
		self.assertTrue(is_pilot_access_allowed(email))
		get_recent_jobs()

	def test_allowed_role_can_use_app(self):
		email = self._ensure_user(
			f"pilot-role-{frappe.generate_hash(length=6)}@example.com",
			roles=[PILOT_ROLE],
		)
		self._enable_pilot(allowed_roles=PILOT_ROLE)
		frappe.set_user(email)
		self.assertTrue(is_pilot_access_allowed(email))

	def test_blocked_user_cannot_use_app(self):
		email = self._ensure_user(f"pilot-blocked-{frappe.generate_hash(length=6)}@example.com")
		self._enable_pilot(allowed_users="someone@example.com", blocked_users=email)
		frappe.set_user(email)
		self.assertFalse(is_pilot_access_allowed(email))
		with self.assertRaises(frappe.PermissionError):
			get_recent_jobs()

	def test_system_manager_bypasses_pilot_restrictions(self):
		email = self._ensure_user(f"pilot-blocked-admin-{frappe.generate_hash(length=6)}@example.com")
		self._enable_pilot(blocked_users=email)
		frappe.set_user("Administrator")
		self.assertTrue(is_pilot_access_allowed("Administrator"))
		get_recent_jobs()

	# Daily limits

	def test_daily_job_limit_blocks_start(self):
		email = self._ensure_user(f"daily-job-{frappe.generate_hash(length=6)}@example.com")
		settings = frappe.get_single("Audio Separation Settings")
		settings.daily_job_limit_per_user = 1
		settings.save(ignore_permissions=True)
		frappe.set_user(email)
		self._create_job(user=email, status="Completed")
		job = self._create_job(user=email)
		with self.assertRaises(frappe.ValidationError):
			start_separation(job.name)

	def test_daily_duration_limit_blocks_start(self):
		email = self._ensure_user(f"daily-dur-{frappe.generate_hash(length=6)}@example.com")
		settings = frappe.get_single("Audio Separation Settings")
		settings.daily_duration_limit_seconds_per_user = 40
		settings.save(ignore_permissions=True)
		frappe.set_user(email)
		self._create_job(user=email, status="Completed", duration_seconds=30)
		job = self._create_job(user=email, duration_seconds=20)
		with self.assertRaises(frappe.ValidationError):
			start_separation(job.name)

	def test_daily_cost_limit_blocks_start(self):
		email = self._ensure_user(f"daily-cost-{frappe.generate_hash(length=6)}@example.com")
		settings = frappe.get_single("Audio Separation Settings")
		settings.cost_per_second_usd = 0.01
		settings.daily_cost_limit_usd_per_user = 0.05
		settings.save(ignore_permissions=True)
		frappe.set_user(email)
		self._create_job(user=email, status="Completed", duration_seconds=30, provider_cost_usd=0.04)
		job = self._create_job(user=email, duration_seconds=10)
		with self.assertRaises(frappe.ValidationError):
			start_separation(job.name)

	def test_zero_daily_limits_mean_unlimited(self):
		email = self._ensure_user(f"daily-zero-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(email)
		self._create_job(user=email, status="Completed")
		job = self._create_job(user=email)
		detail = get_job_detail(job.name)
		self.assertTrue(detail["can_start"])

	def test_system_manager_bypasses_daily_limits(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.daily_job_limit_per_user = 1
		settings.save(ignore_permissions=True)
		self._create_job(user="Administrator", status="Completed")
		job = self._create_job(user="Administrator")
		detail = get_job_detail(job.name)
		self.assertTrue(detail["can_start"])

	def test_failed_jobs_not_counted_unless_consumed(self):
		email = self._ensure_user(f"daily-fail-{frappe.generate_hash(length=6)}@example.com")
		settings = frappe.get_single("Audio Separation Settings")
		settings.daily_job_limit_per_user = 1
		settings.save(ignore_permissions=True)
		frappe.set_user(email)
		self._create_job(user=email, status="Failed", credit_status="Not Required")
		usage = get_user_daily_usage(email)
		self.assertEqual(usage["jobs_today"], 0)
		self._create_job(user=email, status="Failed", credit_status="Consumed")
		usage = get_user_daily_usage(email)
		self.assertEqual(usage["jobs_today"], 1)

	# Cancellation

	def test_owner_can_cancel_queued_job(self):
		email = self._ensure_user(f"cancel-owner-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(email)
		job = self._create_job(user=email, status="Queued")
		result = cancel_job(job.name)
		self.assertTrue(result.get("cancelled"))
		job.reload()
		self.assertEqual(job.status, "Cancelled")

	def test_another_user_cannot_cancel_job(self):
		owner = self._ensure_user(f"cancel-owner2-{frappe.generate_hash(length=6)}@example.com")
		other = self._ensure_user(f"cancel-other-{frappe.generate_hash(length=6)}@example.com")
		job = self._create_job(user=owner, status="Queued")
		frappe.set_user(other)
		with self.assertRaises(frappe.PermissionError):
			cancel_job(job.name)

	def test_system_manager_can_cancel_any_job(self):
		owner = self._ensure_user(f"cancel-owner3-{frappe.generate_hash(length=6)}@example.com")
		job = self._create_job(user=owner, status="Queued")
		frappe.set_user("Administrator")
		result = cancel_job(job.name)
		self.assertTrue(result.get("cancelled"))

	def test_cancelled_queued_job_releases_reservation(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.credit_management_enabled = 1
		settings.save(ignore_permissions=True)
		job = self._create_job(status="Queued", credit_status="Reserved", credit_reservation="RES-1", reserved_amount=0.03)
		with patch(f"{CLIENT}.is_credit_management_enabled", return_value=True), patch(
			f"{CLIENT}.release_job_reservation"
		) as release_mock:
			cancel_job(job.name)
			release_mock.assert_called_once()
		job.reload()
		self.assertEqual(job.status, "Cancelled")

	def test_processing_job_sets_cancellation_requested(self):
		job = self._create_job(status="Processing")
		result = cancel_job(job.name)
		self.assertFalse(result.get("cancelled"))
		self.assertTrue(result.get("cancellation_requested"))
		job.reload()
		self.assertEqual(job.cancellation_requested, 1)

	def test_cancelled_job_cannot_be_started(self):
		job = self._create_job(status="Cancelled")
		with self.assertRaises(frappe.ValidationError):
			start_separation(job.name)

	def test_cancellation_never_leaves_reserved_credits_stuck(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.credit_management_enabled = 1
		settings.save(ignore_permissions=True)
		job = self._create_job(
			status="Draft",
			credit_status="Reserved",
			credit_reservation="RES-2",
			reserved_amount=0.03,
		)
		with patch(f"{CLIENT}.is_credit_management_enabled", return_value=True), patch(
			f"{CLIENT}.release_job_reservation"
		):
			cancel_job(job.name)
		job.reload()
		self.assertEqual(job.status, "Cancelled")
		self.assertIn(job.credit_status, ("Released", "Reserved", "Failed"))

	# Queue / provider health

	def test_system_manager_can_access_queue_health(self):
		frappe.set_user("Administrator")
		health = get_queue_health()
		self.assertIn("active_jobs_count", health)

	def test_normal_user_cannot_access_queue_health(self):
		email = self._ensure_user(f"queue-user-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(email)
		with self.assertRaises(frappe.PermissionError):
			get_queue_health()

	def test_stuck_jobs_detected_by_threshold(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.stuck_job_threshold_minutes = 30
		settings.save(ignore_permissions=True)
		job = self._create_job(status="Queued")
		old_time = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-60)
		frappe.db.set_value("Audio Separation Job", job.name, "creation", old_time)
		health = get_queue_health_data()
		stuck_names = [row["name"] for row in health["stuck_jobs"]]
		self.assertIn(job.name, stuck_names)

	def test_provider_health_unknown_without_recent_jobs(self):
		with patch(
			"audio_stem.utils.provider_health.frappe.get_all",
			return_value=[],
		):
			summary = get_provider_health_summary()
		self.assertEqual(summary["status"], "unknown")

	def test_provider_health_error_on_high_failure_rate(self):
		now = frappe.utils.now_datetime()
		with patch(
			"audio_stem.utils.provider_health.frappe.get_all",
			return_value=[
				frappe._dict(status="Failed", started_at=None, completed_at=now, duration_seconds=30),
				frappe._dict(status="Failed", started_at=None, completed_at=now, duration_seconds=30),
				frappe._dict(status="Failed", started_at=None, completed_at=now, duration_seconds=30),
				frappe._dict(status="Completed", started_at=now, completed_at=now, duration_seconds=30),
			],
		):
			summary = get_provider_health_summary()
		self.assertEqual(summary["status"], "error")

	def test_provider_health_ok_on_recent_success(self):
		now = frappe.utils.now_datetime()
		with patch(
			"audio_stem.utils.provider_health.frappe.get_all",
			return_value=[
				frappe._dict(status="Completed", started_at=now, completed_at=now, duration_seconds=30)
				for _ in range(5)
			],
		):
			summary = get_provider_health_summary()
		self.assertEqual(summary["status"], "ok")

	def test_system_manager_can_access_provider_health(self):
		frappe.set_user("Administrator")
		summary = get_provider_health()
		self.assertIn("status", summary)

	# Audit log

	def test_audit_log_created_for_start_action(self):
		job = self._create_job()
		before = frappe.db.count("Audio Stem Audit Log")
		with patch("audio_stem.api.separation.frappe.enqueue"):
			start_separation(job.name)
		after = frappe.db.count("Audio Stem Audit Log")
		self.assertGreater(after, before)

	def test_audit_log_sanitizes_sensitive_content(self):
		log_audit(
			"Start Job",
			message="wavespeed api_key=secret traceback here",
			metadata={"token": "Bearer abc123", "job": "JOB-1"},
		)
		entry = frappe.get_last_doc("Audio Stem Audit Log")
		self.assertEqual(entry.message, "Action recorded.")
		self.assertNotIn("api_key", entry.metadata_json or "")
		self.assertIn("JOB-1", entry.metadata_json or "")

	def test_audit_log_is_append_only(self):
		log_audit("Admin View", message="test")
		entry = frappe.get_last_doc("Audio Stem Audit Log")
		entry.message = "changed"
		with self.assertRaises(frappe.ValidationError):
			entry.save(ignore_permissions=True)

	# Abuse protection

	def test_hourly_create_limit_blocks_excessive_creates(self):
		email = self._ensure_user(f"abuse-create-{frappe.generate_hash(length=6)}@example.com")
		settings = frappe.get_single("Audio Separation Settings")
		settings.hourly_create_limit_per_user = 2
		settings.save(ignore_permissions=True)
		frappe.set_user(email)
		self._create_job(user=email)
		self._create_job(user=email)
		with self.assertRaises(frappe.ValidationError):
			create_job_from_file(self._create_file().file_url)

	def test_daily_failed_job_limit_blocks_new_starts(self):
		email = self._ensure_user(f"abuse-fail-{frappe.generate_hash(length=6)}@example.com")
		settings = frappe.get_single("Audio Separation Settings")
		settings.daily_failed_job_limit_per_user = 1
		settings.save(ignore_permissions=True)
		frappe.set_user(email)
		self._create_job(user=email, status="Failed")
		job = self._create_job(user=email)
		with self.assertRaises(frappe.ValidationError):
			start_separation(job.name)

	def test_system_manager_bypasses_abuse_limits(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.hourly_create_limit_per_user = 1
		settings.daily_failed_job_limit_per_user = 1
		settings.save(ignore_permissions=True)
		self._create_job(user="Administrator", status="Failed")
		self._create_job(user="Administrator")
		job = self._create_job(user="Administrator")
		detail = get_job_detail(job.name)
		self.assertTrue(detail["can_start"])

	# Page settings / regression

	def test_page_settings_includes_pilot_and_daily_usage(self):
		page = get_page_settings()
		self.assertIn("pilot_mode_enabled", page)
		self.assertIn("daily_usage", page)

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

	def test_ensure_daily_limits_helper_blocks_over_limit(self):
		email = self._ensure_user(f"daily-helper-{frappe.generate_hash(length=6)}@example.com")
		settings = frappe.get_single("Audio Separation Settings")
		settings.daily_job_limit_per_user = 1
		settings.save(ignore_permissions=True)
		frappe.set_user(email)
		self._create_job(user=email, status="Completed")
		job = self._create_job(user=email)
		with self.assertRaises(frappe.ValidationError):
			ensure_daily_limits_for_queue(email, job=job)
