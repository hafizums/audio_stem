# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Milestone 8.5 per-job karaoke style override tests."""

import json
from unittest.mock import patch

import frappe

from audio_stem.api.separation import (
	get_karaoke_style_for_job,
	reset_karaoke_style_for_job,
	update_karaoke_style_for_job,
	update_karaoke_style_settings,
)
from audio_stem.tests.base import temporary_audio_settings
from audio_stem.tests.test_classic_center_karaoke_style import TestClassicCenterKaraokeStyle
from audio_stem.tests.test_milestone8 import TestAudioSeparationMilestone8
from audio_stem.utils.karaoke_style_settings import (
	CLASSIC_CENTER_PRESET,
	resolve_effective_karaoke_style,
)
from audio_stem.utils.karaoke_subtitles import build_karaoke_ass_with_engine, get_karaoke_engine_style_args


class TestMilestone85StyleResolver(TestAudioSeparationMilestone8):
	def _classic_settings(self):
		return temporary_audio_settings(
			karaoke_style_preset=CLASSIC_CENTER_PRESET,
			karaoke_font_size=64,
			karaoke_center_y_percent=50,
		)

	def test_existing_job_defaults_to_global_settings(self):
		with self._classic_settings():
			job = self._create_completed_job()
			resolved = resolve_effective_karaoke_style(job)
			self.assertFalse(resolved["override_enabled"])
			self.assertEqual(resolved["source"], "Global Settings")
			self.assertEqual(resolved["effective"]["karaoke_font_size"], 64)

	def test_override_disabled_uses_global_settings(self):
		with self._classic_settings():
			job = self._create_completed_job()
			job.karaoke_style_override_enabled = 0
			job.karaoke_font_size_override = 90
			job.save(ignore_permissions=True)
			resolved = resolve_effective_karaoke_style(job)
			self.assertEqual(resolved["effective"]["karaoke_font_size"], 64)

	def test_override_enabled_merges_job_fields(self):
		with self._classic_settings() as settings:
			job = self._create_completed_job()
			job.karaoke_style_override_enabled = 1
			job.karaoke_style_preset_override = CLASSIC_CENTER_PRESET
			job.karaoke_font_size_override = 90
			job.karaoke_center_y_percent_override = 40
			job.save(ignore_permissions=True)
			resolved = resolve_effective_karaoke_style(job, settings=settings)
			self.assertEqual(resolved["source"], "Job Override")
			self.assertEqual(resolved["effective"]["karaoke_font_size"], 90)
			self.assertEqual(resolved["effective"]["karaoke_center_y_percent"], 40.0)
			self.assertEqual(resolved["effective"]["karaoke_style_preset"], CLASSIC_CENTER_PRESET)

	def test_empty_override_fields_fall_back_to_global(self):
		with self._classic_settings():
			job = self._create_completed_job()
			job.karaoke_style_override_enabled = 1
			job.karaoke_font_size_override = None
			job.save(ignore_permissions=True)
			resolved = resolve_effective_karaoke_style(job)
			self.assertEqual(resolved["effective"]["karaoke_font_size"], 64)

	def test_invalid_hex_color_rejected(self):
		with self._classic_settings():
			job = self._create_completed_job()
			job.karaoke_style_override_enabled = 1
			job.karaoke_primary_color_override = "bad-color"
			with self.assertRaises(frappe.ValidationError):
				job.save(ignore_permissions=True)

	def test_invalid_font_size_rejected(self):
		with self._classic_settings():
			job = self._create_completed_job()
			job.karaoke_style_override_enabled = 1
			job.karaoke_font_size_override = 10
			with self.assertRaises(frappe.ValidationError):
				job.save(ignore_permissions=True)

	def test_invalid_visible_lines_rejected(self):
		with self._classic_settings():
			job = self._create_completed_job()
			job.karaoke_style_override_enabled = 1
			job.karaoke_visible_lines_override = 9
			with self.assertRaises(frappe.ValidationError):
				job.save(ignore_permissions=True)

	def test_invalid_center_y_rejected(self):
		with self._classic_settings():
			job = self._create_completed_job()
			job.karaoke_style_override_enabled = 1
			job.karaoke_center_y_percent_override = 5
			with self.assertRaises(frappe.ValidationError):
				job.save(ignore_permissions=True)

	def test_invalid_line_gap_rejected(self):
		with self._classic_settings():
			job = self._create_completed_job()
			job.karaoke_style_override_enabled = 1
			job.karaoke_line_gap_override = 10
			with self.assertRaises(frappe.ValidationError):
				job.save(ignore_permissions=True)

	def test_unknown_preset_override_rejected(self):
		with self._classic_settings():
			job = self._create_completed_job()
			job.karaoke_style_override_enabled = 1
			job.karaoke_style_preset_override = "unknown_preset"
			with self.assertRaises(frappe.ValidationError):
				job.save(ignore_permissions=True)


class TestMilestone85KaraokeGeneration(TestClassicCenterKaraokeStyle):
	def test_ass_generation_uses_global_style_when_override_disabled(self):
		self._enable_classic_center_settings()
		job = self._transcribed_job()
		job.karaoke_style_override_enabled = 0
		job.save(ignore_permissions=True)
		style_args = get_karaoke_engine_style_args(job=job)
		self.assertEqual(style_args["classic_center_options"].font_size, 64)

	def test_ass_generation_uses_job_override_when_enabled(self):
		self._enable_classic_center_settings()
		frappe.db.commit()
		job = self._transcribed_job()
		job.karaoke_style_override_enabled = 1
		job.karaoke_style_preset_override = CLASSIC_CENTER_PRESET
		job.karaoke_font_size_override = 96
		job.save(ignore_permissions=True)
		style_args = get_karaoke_engine_style_args(job=job)
		self.assertIn("classic_center_options", style_args)
		self.assertEqual(style_args["classic_center_options"].font_size, 96)

	def test_effective_style_json_saved_after_ass_generation(self):
		self._enable_classic_center_settings()
		job = self._transcribed_job()
		job.karaoke_style_override_enabled = 1
		job.karaoke_font_size_override = 88
		job.save(ignore_permissions=True)
		build_karaoke_ass_with_engine(job)
		self.assertEqual(job.karaoke_style_source, "Job Override")
		self.assertTrue(job.karaoke_effective_style_json)
		stored = json.loads(job.karaoke_effective_style_json)
		self.assertEqual(stored["karaoke_font_size"], 88)

	def test_global_style_source_saved_when_override_disabled(self):
		self._enable_classic_center_settings()
		job = self._transcribed_job()
		build_karaoke_ass_with_engine(job)
		self.assertEqual(job.karaoke_style_source, "Global Settings")


class TestMilestone85Access(TestAudioSeparationMilestone8):
	def test_owner_can_get_job_style(self):
		with temporary_audio_settings(karaoke_style_preset=CLASSIC_CENTER_PRESET):
			job = self._create_completed_job()
			payload = get_karaoke_style_for_job(job.name)
			self.assertIn("effective_style", payload)
			self.assertIn("global_style", payload)

	def test_owner_can_update_own_job_style(self):
		job = self._create_completed_job()
		payload = update_karaoke_style_for_job(
			job.name,
			karaoke_style_override_enabled=1,
			karaoke_font_size_override=80,
		)
		self.assertTrue(payload["override_enabled"])
		self.assertEqual(payload["effective_style"]["karaoke_font_size"], 80)

	def test_another_user_cannot_update_job_style(self):
		owner = self._ensure_user(f"style-owner-{frappe.generate_hash(length=6)}@example.com")
		other = self._ensure_user(f"style-other-{frappe.generate_hash(length=6)}@example.com")
		job = self._create_completed_job(user=owner)
		frappe.set_user(other)
		with self.assertRaises(frappe.PermissionError):
			update_karaoke_style_for_job(job.name, karaoke_style_override_enabled=1)

	def test_system_manager_can_update_any_job_style(self):
		owner = self._ensure_user(f"style-owner2-{frappe.generate_hash(length=6)}@example.com")
		job = self._create_completed_job(user=owner)
		frappe.set_user("Administrator")
		payload = update_karaoke_style_for_job(
			job.name,
			karaoke_style_override_enabled=1,
			karaoke_center_y_percent_override=55,
		)
		self.assertEqual(payload["effective_style"]["karaoke_center_y_percent"], 55.0)

	def test_reset_job_style_clears_override(self):
		job = self._create_completed_job()
		update_karaoke_style_for_job(
			job.name,
			karaoke_style_override_enabled=1,
			karaoke_font_size_override=72,
		)
		payload = reset_karaoke_style_for_job(job.name)
		self.assertFalse(payload["override_enabled"])
		self.assertEqual(payload["style_source"], "Global Settings")

	def test_normal_user_cannot_save_site_wide_style_settings(self):
		user = self._ensure_user(f"style-user-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			update_karaoke_style_settings(karaoke_font_size=70)

	def test_job_style_payload_has_no_server_paths(self):
		job = self._create_completed_job()
		payload = get_karaoke_style_for_job(job.name)
		text = json.dumps(payload)
		self.assertNotIn("/home/", text)
		self.assertNotIn("sites/", text)
		self.assertNotIn("pycaps", text.lower())
