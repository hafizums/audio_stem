# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Shared test helpers.

Tests run against the active bench site database. Use a dedicated test site
when you keep manual Audio Separation Settings on a dev site.
"""

import frappe
from frappe.tests.utils import FrappeTestCase, change_settings

DEFAULT_AUDIO_SEPARATION_TEST_SETTINGS = {
	"enabled": 1,
	"wavespeed_api_key": "test-api-key",
	"max_file_size_mb": 50,
	"max_audio_duration_seconds": 600,
	"cost_per_second_usd": 0.001,
	"store_outputs_locally": 0,
	"credit_management_enabled": 0,
	"credit_type": "AUDIO_STEM",
	"credit_owner_doctype": "User",
	"cleanup_enabled": 0,
	"retention_days": 7,
	"delete_original_after_completion": 0,
	"delete_outputs_after_retention": 0,
	"notify_user_on_completion": 0,
	"notify_user_on_failure": 0,
	"pilot_mode_enabled": 0,
	"allowed_roles": None,
	"allowed_users": None,
	"blocked_users": None,
	"daily_job_limit_per_user": 0,
	"daily_duration_limit_seconds_per_user": 0,
	"daily_cost_limit_usd_per_user": 0,
	"hourly_create_limit_per_user": 0,
	"daily_failed_job_limit_per_user": 0,
	"stuck_job_threshold_minutes": 30,
	"openai_enabled": 0,
	"openai_api_key": "test-openai-key",
	"transcription_model": "whisper-1",
	"transcription_max_file_size_mb": 25,
	"transcription_cost_per_minute_usd": 0,
	"default_transcription_language": None,
	"enable_word_timestamps": 1,
	"charge_credits_for_transcription": 0,
	"karaoke_enabled": 0,
	"karaoke_default_template": "hype",
	"karaoke_output_width": 1080,
	"karaoke_output_height": 1920,
	"karaoke_background_color": "#111111",
	"karaoke_include_instrumental_audio": 1,
	"charge_credits_for_karaoke": 0,
}


class AudioStemTestCase(FrappeTestCase):
	"""Applies temporary test settings and restores them after each test."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self._settings_ctx = change_settings(
			"Audio Separation Settings",
			dict(DEFAULT_AUDIO_SEPARATION_TEST_SETTINGS),
		)
		self._settings_ctx.__enter__()
		self.addCleanup(self._settings_ctx.__exit__, None, None, None)

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()
