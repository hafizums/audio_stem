# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe

from audio_stem.api.separation import (
	get_job_detail,
	get_transcript_for_edit,
	regenerate_subtitle_assets,
	reset_manual_transcript,
	save_transcript_corrections,
	start_karaoke_render,
)
from audio_stem.tests.test_milestone8 import FORBIDDEN_CREDIT_PATTERNS, SAMPLE_TRANSCRIPT, TestAudioSeparationMilestone8
from audio_stem.utils.karaoke_subtitles import build_karaoke_ass_with_engine, resolve_transcript_json_for_karaoke
from audio_stem.utils.transcription_karaoke_controls import can_start_karaoke
from audio_stem.utils.transcript_corrections import (
	approve_manual_transcript,
	load_transcript_for_edit,
	normalize_edit_payload,
	prepare_transcript_for_karaoke,
	resolve_karaoke_rendered_transcript_label,
	resolve_karaoke_transcript_label,
	resolve_karaoke_transcript_source,
	sanitize_transcript_text,
	save_manual_transcript,
	shift_timings,
	snap_word_overlaps,
	validate_transcript_edit_payload,
)
from audio_stem.utils.transcription_assets import write_transcript_json

APP_ROOT = Path(__file__).resolve().parents[1]


class TestAudioSeparationMilestone82(TestAudioSeparationMilestone8):
	def _transcribed_job(self):
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		job.transcript_text = SAMPLE_TRANSCRIPT["text"]
		job.save(ignore_permissions=True)
		return job

	def _manual_payload(self, **overrides):
		payload = normalize_edit_payload(dict(SAMPLE_TRANSCRIPT))
		payload.update(overrides)
		return payload

	def test_owner_can_get_transcript_for_edit(self):
		job = self._transcribed_job()
		result = get_transcript_for_edit(job.name)
		self.assertEqual(result["source"], "whisper")
		self.assertIn("transcript", result)

	def test_other_user_cannot_get_transcript_for_edit(self):
		owner = self._ensure_user("m82-owner@example.com")
		other = self._ensure_user("m82-other@example.com")
		job = self._transcribed_job()
		job.user = owner
		job.save(ignore_permissions=True)
		frappe.set_user(other)
		with self.assertRaises(frappe.PermissionError):
			get_transcript_for_edit(job.name)

	def test_system_manager_can_get_transcript_for_edit(self):
		owner = self._ensure_user("m82-sm-owner@example.com")
		job = self._transcribed_job()
		job.user = owner
		job.save(ignore_permissions=True)
		frappe.set_user("Administrator")
		result = get_transcript_for_edit(job.name)
		self.assertIn("transcript", result)

	def test_save_manual_does_not_overwrite_whisper_assets(self):
		job = self._transcribed_job()
		original_json = job.transcript_json_file
		original_srt = job.transcript_srt_file
		original_vtt = job.transcript_vtt_file
		original_text = job.transcript_text

		save_manual_transcript(job, self._manual_payload(text="corrected lyrics"))
		job.reload()

		self.assertEqual(job.transcript_json_file, original_json)
		self.assertEqual(job.transcript_srt_file, original_srt)
		self.assertEqual(job.transcript_vtt_file, original_vtt)
		self.assertEqual(job.transcript_text, original_text)
		self.assertTrue(job.manual_transcript_json_file)
		self.assertNotEqual(job.manual_transcript_json_file, original_json)

	def test_reset_manual_preserves_whisper(self):
		job = self._transcribed_job()
		original_json = job.transcript_json_file
		save_manual_transcript(job, self._manual_payload(text="edited"))
		reset_manual_transcript(job.name)
		job.reload()
		self.assertEqual(job.transcript_json_file, original_json)
		self.assertEqual(job.manual_transcript_status, "Not Started")
		self.assertFalse(job.manual_transcript_json_file)

	def test_rejects_negative_timestamps(self):
		payload = self._manual_payload()
		payload["segments"][0]["start"] = -1
		with self.assertRaises(frappe.ValidationError):
			validate_transcript_edit_payload(payload)

	def test_rejects_end_before_start(self):
		payload = self._manual_payload()
		payload["segments"][0]["start"] = 5
		payload["segments"][0]["end"] = 2
		with self.assertRaises(frappe.ValidationError):
			validate_transcript_edit_payload(payload)

	def test_rejects_unsafe_html(self):
		with self.assertRaises(frappe.ValidationError):
			sanitize_transcript_text("<script>alert(1)</script>")

	def test_accepts_valid_segment_edits(self):
		payload = self._manual_payload()
		payload["segments"][0]["text"] = "hello corrected"
		validate_transcript_edit_payload(payload)

	def test_accepts_valid_word_level_edits(self):
		payload = self._manual_payload()
		payload["segments"][0]["words"] = [{"text": "hello", "start": 0.0, "end": 1.0}]
		validate_transcript_edit_payload(payload)

	def test_save_creates_private_manual_assets(self):
		job = self._transcribed_job()
		save_manual_transcript(job, self._manual_payload())
		job.reload()
		for field in ("manual_transcript_json_file", "manual_transcript_srt_file", "manual_transcript_vtt_file"):
			file_name = frappe.db.get_value("File", {"file_url": job.get(field)}, "name")
			self.assertEqual(frappe.db.get_value("File", file_name, "is_private"), 1)

	def test_approve_sets_approved_fields(self):
		job = self._transcribed_job()
		save_manual_transcript(job, self._manual_payload())
		approve_manual_transcript(job)
		job.reload()
		self.assertEqual(job.manual_transcript_status, "Approved")
		self.assertTrue(job.manual_transcript_approved_at)
		self.assertTrue(job.manual_transcript_approved_by)

	def test_regenerate_subtitles_uses_manual_source(self):
		job = self._transcribed_job()
		save_manual_transcript(job, self._manual_payload())
		regenerate_subtitle_assets(job.name, source="manual")
		job.reload()
		self.assertTrue(job.manual_transcript_srt_file)
		self.assertTrue(job.manual_transcript_vtt_file)

	def test_auto_uses_approved_manual_transcript(self):
		job = self._transcribed_job()
		save_manual_transcript(job, self._manual_payload())
		approve_manual_transcript(job)
		job.karaoke_source_mode = "Auto"
		job.karaoke_use_manual_transcript = 1
		job.save(ignore_permissions=True)
		path = resolve_karaoke_transcript_source(job)
		self.assertEqual(path, frappe.get_doc("File", {"file_url": job.manual_transcript_json_file}).get_full_path())

	def test_auto_falls_back_to_whisper_without_manual(self):
		job = self._transcribed_job()
		job.karaoke_source_mode = "Auto"
		path = resolve_karaoke_transcript_source(job)
		self.assertEqual(path, frappe.get_doc("File", {"file_url": job.transcript_json_file}).get_full_path())

	def test_manual_mode_requires_manual_transcript(self):
		job = self._transcribed_job()
		job.karaoke_source_mode = "Manual Corrected"
		job.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			resolve_karaoke_transcript_source(job)

	def test_original_whisper_mode_ignores_manual(self):
		job = self._transcribed_job()
		save_manual_transcript(job, self._manual_payload())
		approve_manual_transcript(job)
		job.karaoke_source_mode = "Original Whisper"
		job.save(ignore_permissions=True)
		path = resolve_karaoke_transcript_source(job)
		self.assertEqual(path, frappe.get_doc("File", {"file_url": job.transcript_json_file}).get_full_path())

	def test_ass_generation_uses_selected_transcript_source(self):
		from audio_stem.utils.karaoke_subtitles import is_karaoke_engine_available

		if not is_karaoke_engine_available():
			self.skipTest("karaoke_engine is not installed")
		job = self._transcribed_job()
		save_manual_transcript(job, self._manual_payload())
		approve_manual_transcript(job)
		job.karaoke_source_mode = "Manual Corrected"
		job.save(ignore_permissions=True)
		build_karaoke_ass_with_engine(job)
		self.assertEqual(job.karaoke_source_transcript_file, job.manual_transcript_json_file)

	def test_segment_text_edit_syncs_words_for_karaoke(self):
		payload = {
			"text": "hello world",
			"segments": [
				{
					"start": 0.0,
					"end": 5.0,
					"text": "hello corrected",
					"words": [
						{"text": "hello", "start": 0.0, "end": 2.0},
						{"text": "world", "start": 2.0, "end": 5.0},
					],
				}
			],
			"words": [
				{"text": "hello", "start": 0.0, "end": 2.0},
				{"text": "world", "start": 2.0, "end": 5.0},
			],
		}
		prepared = prepare_transcript_for_karaoke(payload)
		self.assertEqual(prepared["segments"][0]["text"], "hello corrected")
		self.assertEqual(
			[word["text"] for word in prepared["segments"][0]["words"]],
			["hello", "corrected"],
		)
		self.assertEqual(
			[word["text"] for word in prepared["words"]],
			["hello", "corrected"],
		)

	def test_save_manual_persists_synced_segment_words(self):
		job = self._transcribed_job()
		payload = normalize_edit_payload(dict(SAMPLE_TRANSCRIPT))
		payload["segments"][0]["words"] = [{"text": "hello", "start": 0.0, "end": 1.0}]
		payload["segments"][1]["words"] = [{"text": "world", "start": 2.5, "end": 5.0}]
		payload["segments"][0]["text"] = "hello corrected"
		save_manual_transcript(job, payload)
		job.reload()
		from audio_stem.utils.transcript_corrections import _load_json_file

		manual = _load_json_file(job.manual_transcript_json_file)
		self.assertEqual(manual["segments"][0]["text"], "hello corrected")
		self.assertEqual(
			[word.get("text") or word.get("word") for word in manual["segments"][0]["words"]],
			["hello", "corrected"],
		)

	def test_can_start_karaoke_when_already_completed(self):
		self._enable_karaoke()
		job = self._transcribed_job()
		job.karaoke_status = "Completed"
		job.save(ignore_permissions=True)
		can_start, reason = can_start_karaoke(job)
		self.assertTrue(can_start)
		self.assertIsNone(reason)

	def test_rendered_transcript_label_tracks_manual_source(self):
		job = self._transcribed_job()
		save_manual_transcript(job, self._manual_payload())
		approve_manual_transcript(job)
		job.karaoke_source_mode = "Manual Corrected"
		job.save(ignore_permissions=True)
		build_karaoke_ass_with_engine(job)
		self.assertEqual(resolve_karaoke_rendered_transcript_label(job), "Manual Corrected")

	def test_start_karaoke_render_allows_completed_regeneration(self):
		self._enable_karaoke()
		job = self._transcribed_job()
		save_manual_transcript(job, self._manual_payload())
		approve_manual_transcript(job)
		job.karaoke_status = "Completed"
		job.save(ignore_permissions=True)
		with patch("audio_stem.api.separation.frappe.enqueue") as enqueue_mock:
			result = start_karaoke_render(job.name, karaoke_source_mode="Manual Corrected")
		self.assertFalse(result.get("already_active"))
		enqueue_mock.assert_called_once()

	def test_job_payload_includes_manual_status_fields(self):
		job = self._transcribed_job()
		save_manual_transcript(job, self._manual_payload())
		detail = get_job_detail(job.name)
		self.assertIn("manual_transcript_status", detail)
		self.assertIn("has_manual_transcript", detail)
		self.assertIn("karaoke_source_mode", detail)
		self.assertIn("karaoke_transcript_source_label", detail)

	def test_job_payload_does_not_expose_openai_key(self):
		self._enable_openai()
		job = self._transcribed_job()
		detail = get_job_detail(job.name)
		payload = json.dumps({key: detail.get(key) for key in detail if key != "creation"})
		self.assertNotIn("test-openai-key", payload)
		self.assertNotIn("sk-", payload)

	def test_shift_all_timings_works(self):
		payload = self._manual_payload()
		shifted = shift_timings(payload, 1.5)
		self.assertEqual(shifted["segments"][0]["start"], 1.5)

	def test_invalid_shifted_timings_rejected(self):
		payload = self._manual_payload()
		payload["segments"][0]["start"] = 0.0
		payload["segments"][0]["end"] = 0.5
		payload["segments"][0]["words"] = [{"text": "hello", "start": 0.0, "end": 0.5}]
		with self.assertRaises(frappe.ValidationError):
			shift_timings(payload, -5)

	def test_snap_overlaps_fixes_minor_overlaps(self):
		words = [
			{"text": "a", "start": 0.0, "end": 1.0},
			{"text": "b", "start": 0.9, "end": 1.5},
		]
		result = snap_word_overlaps(words)
		self.assertGreaterEqual(result[1]["start"], result[0]["end"])

	def test_save_transcript_corrections_api(self):
		job = self._transcribed_job()
		payload = self._manual_payload(text="api corrected")
		result = save_transcript_corrections(job.name, json.dumps(payload))
		self.assertEqual(result["manual_transcript_status"], "Saved")

	def test_start_karaoke_manual_mode_requires_manual(self):
		self._enable_karaoke()
		job = self._transcribed_job()
		with self.assertRaises(frappe.ValidationError):
			with patch("audio_stem.api.separation.frappe.enqueue"):
				start_karaoke_render(job.name, karaoke_source_mode="Manual Corrected")

	def test_resolve_label_for_auto_manual(self):
		job = self._transcribed_job()
		save_manual_transcript(job, self._manual_payload())
		approve_manual_transcript(job)
		job.karaoke_source_mode = "Auto"
		job.karaoke_use_manual_transcript = 1
		job.save(ignore_permissions=True)
		self.assertEqual(resolve_karaoke_transcript_label(job), "Manual Corrected")

	def test_runtime_modules_do_not_import_pycaps_or_playwright(self):
		forbidden = ("import pycaps", "from pycaps", "import playwright", "from playwright")
		hits = []
		for path in APP_ROOT.rglob("*.py"):
			if "tests" in path.parts:
				continue
			text = path.read_text(encoding="utf-8", errors="ignore")
			for pattern in forbidden:
				if pattern in text:
					hits.append(f"{path}: {pattern}")
		self.assertEqual(hits, [])

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

	def test_resolve_transcript_json_for_karaoke_uses_source_resolution(self):
		job = self._transcribed_job()
		payload = self._manual_payload()
		payload["segments"][0]["words"] = [{"text": "hello", "start": 0.0, "end": 1.0}]
		payload["segments"][1]["words"] = [{"text": "world", "start": 2.5, "end": 5.0}]
		payload["segments"][0]["text"] = "hello corrected"
		save_manual_transcript(job, payload)
		approve_manual_transcript(job)
		job.karaoke_source_mode = "Manual Corrected"
		job.save(ignore_permissions=True)
		manual_path = resolve_karaoke_transcript_source(job)
		prepared_path = resolve_transcript_json_for_karaoke(job)
		self.assertTrue(os.path.exists(prepared_path))
		self.assertNotEqual(prepared_path, manual_path)
		with open(prepared_path, encoding="utf-8") as handle:
			prepared = json.load(handle)
		self.assertEqual(prepared["segments"][0]["text"], "hello corrected")
		os.unlink(prepared_path)
