# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Milestone 8.4 — UI redesign guarantees.

Backend-enforced guarantees that back the redesigned /audio-vocal-remover UI:
- Admin tools (checklist, queue, provider, usage) require System Manager.
- The page settings + job detail payloads never expose forbidden wording
  (PyCaps, Playwright, Chromium) or raw provider secrets.
- Non-system users never see raw local file paths in the page settings payload.
- The built frontend bundle does not reference forbidden libraries.
"""

import os
from pathlib import Path

import frappe

from audio_stem.api import admin as admin_api
from audio_stem.api.separation import get_job_detail, get_page_settings
from audio_stem.tests.base import AudioStemTestCase
from audio_stem.tests.test_milestone8 import TestAudioSeparationMilestone8

FORBIDDEN_UI_WORDS = ("pycaps", "playwright", "chromium")
APP_ROOT = Path(__file__).resolve().parents[1]  # python package "audio_stem"
APP_ROOT_DIR = Path(__file__).resolve().parents[2]  # app root containing pyproject.toml
PUBLIC_DIR = APP_ROOT_DIR / "audio_stem" / "public" / "audio-vocal-remover"


class TestMilestone84UiRedesign(TestAudioSeparationMilestone8):
	def test_admin_checklist_requires_system_manager(self):
		owner = self._ensure_user("m84-owner@example.com")
		frappe.set_user(owner)
		with self.assertRaises(frappe.PermissionError):
			admin_api.get_configuration_checklist()

	def test_admin_queue_health_requires_system_manager(self):
		owner = self._ensure_user("m84-queue@example.com")
		frappe.set_user(owner)
		with self.assertRaises(frappe.PermissionError):
			admin_api.get_queue_health()

	def test_admin_provider_health_requires_system_manager(self):
		owner = self._ensure_user("m84-provider@example.com")
		frappe.set_user(owner)
		with self.assertRaises(frappe.PermissionError):
			admin_api.get_provider_health()

	def test_admin_usage_summary_requires_system_manager(self):
		owner = self._ensure_user("m84-usage@example.com")
		frappe.set_user(owner)
		with self.assertRaises(frappe.PermissionError):
			admin_api.get_audio_stem_usage_summary()

	def test_admin_endpoints_callable_by_system_manager(self):
		# Administrator should be able to call all admin endpoints without raising.
		frappe.set_user("Administrator")
		self.assertIsInstance(admin_api.get_configuration_checklist(), list)
		self.assertIsInstance(admin_api.get_queue_health(), dict)
		self.assertIsInstance(admin_api.get_provider_health(), dict)
		self.assertIsInstance(admin_api.get_audio_stem_usage_summary(), dict)
		self.assertIsInstance(admin_api.get_credit_reconciliation_issues(), list)

	def test_page_settings_payload_has_no_forbidden_ui_words(self):
		payload = get_page_settings()
		text = str(payload).lower()
		for word in FORBIDDEN_UI_WORDS:
			self.assertNotIn(word, text)

	def test_page_settings_payload_does_not_expose_api_keys(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.openai_enabled = 1
		settings.openai_api_key = "sk-test-m84-secret"
		settings.wavespeed_api_key = "ws-test-m84-secret"
		settings.save(ignore_permissions=True)
		frappe.db.commit()

		payload = get_page_settings()
		text = str(payload)
		self.assertNotIn("sk-test-m84-secret", text)
		self.assertNotIn("ws-test-m84-secret", text)

	def test_page_settings_includes_admin_visibility_flag(self):
		payload = get_page_settings()
		self.assertIn("is_system_manager", payload)

	def test_job_detail_payload_has_no_forbidden_ui_words(self):
		job = self._create_completed_job()
		job.karaoke_background_video_file = self._attach_test_file(
			content=b"video", suffix=".mp4", label="m84-bg"
		)
		job.save(ignore_permissions=True)
		detail = get_job_detail(job.name)
		text = str(detail).lower()
		for word in FORBIDDEN_UI_WORDS:
			self.assertNotIn(word, text)

	def test_job_detail_payload_does_not_expose_local_file_path(self):
		from audio_stem.utils.files import resolve_frappe_file_path

		job = self._create_completed_job()
		file_url = self._attach_test_file(content=b"video", suffix=".mp4", label="m84-path")
		job.karaoke_background_video_file = file_url
		job.save(ignore_permissions=True)

		detail = get_job_detail(job.name)
		local_path = resolve_frappe_file_path(file_url)
		if local_path:
			self.assertNotIn(local_path, str(detail))

	def test_built_frontend_bundle_has_no_forbidden_libraries(self):
		"""The shipped JS bundle must not reference PyCaps/Playwright/Chromium."""
		if not PUBLIC_DIR.exists():
			self.skipTest("Frontend bundle has not been built yet.")
		offenders = []
		for root, _dirs, files in os.walk(PUBLIC_DIR):
			for name in files:
				if not name.endswith((".js", ".css", ".html")):
					continue
				path = os.path.join(root, name)
				try:
					with open(path, "r", encoding="utf-8") as handle:
						content = handle.read().lower()
				except OSError:
					continue
				for word in FORBIDDEN_UI_WORDS:
					if word in content:
						offenders.append((path, word))
		self.assertFalse(offenders, f"Forbidden UI references found: {offenders}")

	def test_runtime_modules_do_not_import_forbidden_libraries(self):
		"""Source modules must not import PyCaps/Playwright/Chromium."""
		src_dir = APP_ROOT / "audio_stem"
		offenders = []
		for root, _dirs, files in os.walk(src_dir):
			for name in files:
				if not name.endswith(".py"):
					continue
				if name.startswith("test_milestone8_4"):
					continue
				path = os.path.join(root, name)
				try:
					with open(path, "r", encoding="utf-8") as handle:
						content = handle.read().lower()
				except OSError:
					continue
				for forbidden in ("import pycaps", "from pycaps", "import playwright", "from playwright"):
					if forbidden in content:
						offenders.append((path, forbidden))
		self.assertFalse(offenders, f"Forbidden imports found: {offenders}")

	def test_existing_jobs_still_render_payload_for_owner(self):
		job = self._create_completed_job()
		owner = job.user
		frappe.set_user(owner)
		detail = get_job_detail(job.name)
		self.assertEqual(detail["name"], job.name)
		self.assertIn("karaoke_video_render_enabled", detail)
