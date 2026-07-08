# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Shared test helpers.

Tests snapshot and restore Audio Separation Settings around each test and
clean up only records tagged as test data. Prefer a dedicated test site:

  bench --site audio-stem-test.local run-tests --app audio_stem

Do not run tests on a live/manual site such as jomveo unless you accept that
tests will temporarily change settings during execution (they are restored after).
"""

import frappe
from frappe.exceptions import QueryTimeoutError
from frappe.tests.utils import FrappeTestCase, change_settings
from unittest.mock import patch

from audio_stem.tests.test_utils import (
	cleanup_audio_stem_test_data,
	create_test_file_doc,
	create_test_file_from_temp,
	create_test_job_doc,
	ensure_test_user,
	restore_audio_settings,
	snapshot_audio_settings,
	temporary_audio_settings,
)

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
	"subtitle_max_words_per_line": 5,
	"subtitle_max_line_duration_seconds": 4.0,
	"subtitle_min_word_duration_seconds": 0.08,
	"subtitle_snap_overlaps": 1,
	"charge_credits_for_transcription": 0,
	"karaoke_enabled": 0,
	"karaoke_ass_enabled": 1,
	"karaoke_video_render_enabled": 0,
	"karaoke_style_preset": "default_1080p",
	"karaoke_visible_lines": 3,
	"karaoke_center_y_percent": 50.0,
	"karaoke_line_gap": 90,
	"karaoke_font_name": "Helvetica",
	"karaoke_font_size": 64,
	"karaoke_primary_color": "#FFFFFF",
	"karaoke_highlight_color": "#3366FF",
	"karaoke_previous_line_color": "#3366FF",
	"karaoke_next_line_color": "#FFFFFF",
	"karaoke_outline_color": "#000000",
	"karaoke_shadow": 1.0,
	"karaoke_outline": 3.0,
	"karaoke_max_words_per_line": 5,
	"karaoke_video_width": 1080,
	"karaoke_video_height": 1920,
	"karaoke_background_color": "#111111",
	"default_karaoke_background_video": None,
	"allow_user_karaoke_background_upload": 1,
	"karaoke_ignore_background_audio": 1,
	"karaoke_loop_background_video": 1,
	"karaoke_background_blur": 0,
	"karaoke_background_darken": 0,
	"karaoke_background_darken_opacity": 0.25,
	"karaoke_background_fit_mode": "Cover",
	"karaoke_include_instrumental_audio": 1,
	"karaoke_ffmpeg_preset": "veryfast",
	"karaoke_ffmpeg_crf": 18,
	"karaoke_ffmpeg_timeout_seconds": 1800,
	"charge_credits_for_karaoke": 0,
}


class AudioStemTestCase(FrappeTestCase):
	"""Snapshots settings, applies test defaults, and cleans tagged test data."""

	_settings_snapshot: dict | None = None
	_settings_ctx = None
	_user_throttle_patch = None

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self._user_throttle_patch = patch(
			"frappe.core.doctype.user.user.throttle_user_creation",
			lambda: None,
		)
		self._user_throttle_patch.start()
		cleanup_audio_stem_test_data()
		self._settings_snapshot = snapshot_audio_settings()
		self._settings_ctx = change_settings(
			"Audio Separation Settings",
			dict(DEFAULT_AUDIO_SEPARATION_TEST_SETTINGS),
		)
		self._settings_ctx.__enter__()

	def tearDown(self):
		try:
			if self._settings_ctx is not None:
				self._settings_ctx.__exit__(None, None, None)
		finally:
			try:
				restore_audio_settings(self._settings_snapshot)
			finally:
				try:
					cleanup_audio_stem_test_data()
				except QueryTimeoutError:
					frappe.db.rollback()
					cleanup_audio_stem_test_data()
				frappe.set_user("Administrator")
				if self._user_throttle_patch is not None:
					self._user_throttle_patch.stop()
					self._user_throttle_patch = None
		super().tearDown()

	def _create_file(self, content: bytes = b"audio", suffix: str = ".mp3", label: str = "input"):
		return create_test_file_from_temp(content=content, suffix=suffix, label=label)

	def _attach_test_file(self, content: bytes = b"fake-audio-content", suffix: str = ".mp3", label: str = "input"):
		return create_test_file_doc(content=content, suffix=suffix, label=label).file_url

	def _create_completed_job(self, user=None):
		return create_test_job_doc(user=user, with_outputs=True)

	def _create_job(self, with_file: bool = True, status: str = "Draft"):
		if with_file:
			return create_test_job_doc(user=frappe.session.user, status=status, with_outputs=True)
		return create_test_job_doc(
			user=frappe.session.user,
			status=status,
			with_outputs=False,
			original_file=None,
			vocal_file=None,
			instrumental_file=None,
		)

	def _ensure_user(self, email: str):
		return ensure_test_user(email)


__all__ = [
	"DEFAULT_AUDIO_SEPARATION_TEST_SETTINGS",
	"AudioStemTestCase",
	"temporary_audio_settings",
]
