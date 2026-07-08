# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Gate 10.6 — Karaoke highlight timing granularity."""

import json

import frappe

from audio_stem.tests.test_milestone8 import SAMPLE_TRANSCRIPT, TestAudioSeparationMilestone8
from audio_stem.utils.karaoke_style_settings import (
	apply_job_style_update,
	resolve_effective_karaoke_style,
	validate_karaoke_style_settings,
)
from audio_stem.utils.karaoke_subtitles import (
	_segment_options_from_settings,
	build_karaoke_ass_with_engine,
	is_karaoke_engine_available,
)
from audio_stem.utils.transcription_assets import write_transcript_json


class TestGate106KaraokeTiming(TestAudioSeparationMilestone8):
	def setUp(self):
		super().setUp()
		if not is_karaoke_engine_available():
			self.skipTest("karaoke_engine is not installed")
		self._enable_karaoke()

	def _transcribed_job(self):
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		job.transcript_text = SAMPLE_TRANSCRIPT["text"]
		job.save(ignore_permissions=True)
		return job

	def _lalalili_transcript(self):
		return {
			"text": "LALALILI",
			"segments": [
				{
					"start": 0.0,
					"end": 1.0,
					"text": "LALALILI",
					"words": [
						{"word": "LALALILI", "start": 0.0, "end": 1.0},
					],
				}
			],
		}

	def test_settings_default_to_word_timing(self):
		settings = frappe.get_single("Audio Separation Settings")
		self.assertEqual(settings.karaoke_timing_granularity or "word", "word")
		self.assertEqual(settings.karaoke_syllable_mode or "auto", "auto")
		validate_karaoke_style_settings(settings)

	def test_segment_options_pass_timing_mode_to_karaoke_engine(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_timing_granularity = "syllable"
		settings.karaoke_syllable_mode = "auto"
		settings.save(ignore_permissions=True)
		frappe.db.commit()

		options = _segment_options_from_settings()
		self.assertEqual(options.karaoke_timing_granularity, "syllable")
		self.assertEqual(options.karaoke_syllable_mode, "auto")

	def test_per_job_timing_override(self):
		job = self._transcribed_job()
		apply_job_style_update(
			job,
			{
				"karaoke_style_override_enabled": 1,
				"karaoke_timing_granularity_override": "syllable",
			},
		)
		job.save(ignore_permissions=True)
		frappe.db.commit()

		resolved = resolve_effective_karaoke_style(job)
		self.assertEqual(resolved["effective"]["karaoke_timing_granularity"], "syllable")

		options = _segment_options_from_settings(job)
		self.assertEqual(options.karaoke_timing_granularity, "syllable")

	def test_syllable_mode_generates_more_kf_tags(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_style_preset = "classic_center_3line"
		settings.karaoke_timing_granularity = "word"
		settings.save(ignore_permissions=True)
		frappe.db.commit()

		job = self._transcribed_job()
		write_transcript_json(job, self._lalalili_transcript())
		job.save(ignore_permissions=True)

		build_karaoke_ass_with_engine(job)
		word_ass = frappe.get_doc("File", {"file_url": job.karaoke_ass_file})
		word_content = word_ass.get_content()

		settings.karaoke_timing_granularity = "syllable"
		settings.save(ignore_permissions=True)
		frappe.db.commit()

		build_karaoke_ass_with_engine(job)
		syllable_ass = frappe.get_doc("File", {"file_url": job.karaoke_ass_file})
		syllable_content = syllable_ass.get_content()

		self.assertGreater(
			syllable_content.count(r"{\kf"),
			word_content.count(r"{\kf"),
		)

	def test_word_mode_output_remains_backward_compatible(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_timing_granularity = "word"
		settings.save(ignore_permissions=True)
		frappe.db.commit()

		job = self._transcribed_job()
		build_karaoke_ass_with_engine(job)
		stored = json.loads(job.karaoke_effective_style_json)
		self.assertEqual(stored["karaoke_timing_granularity"], "word")

	def test_effective_style_json_stores_timing_mode(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_timing_granularity = "syllable"
		settings.karaoke_syllable_mode = "auto"
		settings.save(ignore_permissions=True)
		frappe.db.commit()

		job = self._transcribed_job()
		build_karaoke_ass_with_engine(job)
		stored = json.loads(job.karaoke_effective_style_json)
		self.assertEqual(stored["karaoke_timing_granularity"], "syllable")
		self.assertEqual(stored["karaoke_syllable_mode"], "auto")
