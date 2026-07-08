# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe

from audio_stem.tests.base import AudioStemTestCase
from audio_stem.tests.test_utils import (
	TEST_AUDIO_STEM_MARKER,
	cleanup_audio_stem_test_data,
	create_test_job_doc,
)


class TestAudioStemCleanupGuard(AudioStemTestCase):
	"""Verify cleanup only removes clearly tagged test records."""

	def test_cleanup_deletes_only_test_marked_jobs(self):
		normal_job = frappe.get_doc(
			{
				"doctype": "Audio Separation Job",
				"user": frappe.session.user,
				"status": "Draft",
				"original_filename": "real_user_song.mp3",
				"cleanup_notes": "manual user job",
			}
		)
		normal_job.insert(ignore_permissions=True)

		test_job = create_test_job_doc(status="Draft", with_outputs=False)
		frappe.db.commit()

		cleanup_audio_stem_test_data()

		self.assertTrue(frappe.db.exists("Audio Separation Job", normal_job.name))
		self.assertFalse(frappe.db.exists("Audio Separation Job", test_job.name))

		frappe.delete_doc("Audio Separation Job", normal_job.name, force=True, ignore_permissions=True)

	def test_cleanup_matches_test_marker_in_filename(self):
		normal_job = frappe.get_doc(
			{
				"doctype": "Audio Separation Job",
				"user": frappe.session.user,
				"status": "Draft",
				"original_filename": "my_recording.mp3",
			}
		)
		normal_job.insert(ignore_permissions=True)

		marked_job = frappe.get_doc(
			{
				"doctype": "Audio Separation Job",
				"user": frappe.session.user,
				"status": "Draft",
				"original_filename": "test_audio_stem_input.mp3",
				"cleanup_notes": TEST_AUDIO_STEM_MARKER,
			}
		)
		marked_job.insert(ignore_permissions=True)
		frappe.db.commit()

		cleanup_audio_stem_test_data()

		self.assertTrue(frappe.db.exists("Audio Separation Job", normal_job.name))
		self.assertFalse(frappe.db.exists("Audio Separation Job", marked_job.name))

		frappe.delete_doc("Audio Separation Job", normal_job.name, force=True, ignore_permissions=True)
