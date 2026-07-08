# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""One-off cleanup for legacy untagged audio_stem test data.

Safe patterns only:
- users: audio-user-*@example.com, *@audio-stem-test.example
- jobs/files owned by those users
- tagged TEST_AUDIO_STEM / test_audio_stem_* records
- empty draft jobs and draft jobs using tmp*.mp3 unittest files
"""

from __future__ import annotations

import frappe

from audio_stem.tests.test_utils import TEST_AUDIO_STEM_MARKER, cleanup_audio_stem_test_data

AUDIO_USER_PATTERN = "%audio-user-%@example.com"
AUDIO_STEM_TEST_USER_PATTERN = "%@audio-stem-test.example"
AUDIO_USER_FILE_PATTERN = "audio-user-%"


def _test_user_patterns() -> tuple[str, str]:
	return AUDIO_USER_PATTERN, AUDIO_STEM_TEST_USER_PATTERN


def get_test_job_names() -> list[str]:
	p1, p2 = _test_user_patterns()
	return frappe.db.sql_list(
		"""
		SELECT name FROM `tabAudio Separation Job`
		WHERE user LIKE %s OR user LIKE %s
		""",
		(p1, p2),
	)


def get_empty_draft_test_job_names() -> list[str]:
	"""Draft jobs with no attached files/outputs — typical unittest leftovers."""
	return frappe.db.sql_list(
		"""
		SELECT name FROM `tabAudio Separation Job`
		WHERE status = 'Draft'
		  AND IFNULL(original_file, '') = ''
		  AND IFNULL(original_filename, '') = ''
		  AND IFNULL(vocal_file, '') = ''
		  AND IFNULL(vocal_output_url, '') = ''
		  AND IFNULL(instrumental_file, '') = ''
		  AND IFNULL(instrumental_output_url, '') = ''
		  AND IFNULL(transcript_json_file, '') = ''
		  AND IFNULL(manual_transcript_json_file, '') = ''
		  AND IFNULL(karaoke_ass_file, '') = ''
		  AND IFNULL(karaoke_video_file, '') = ''
		  AND IFNULL(cleanup_notes, '') NOT IN ('manual user job')
		"""
	)


def get_tempfile_test_job_names() -> list[str]:
	"""Draft jobs whose attached audio files are unittest temp names (tmp*.mp3)."""
	return frappe.db.sql_list(
		"""
		SELECT DISTINCT j.name
		FROM `tabAudio Separation Job` j
		LEFT JOIN tabFile original_file ON original_file.file_url = j.original_file
		LEFT JOIN tabFile vocal_file ON vocal_file.file_url = j.vocal_file
		LEFT JOIN tabFile instrumental_file ON instrumental_file.file_url = j.instrumental_file
		WHERE j.status = 'Draft'
		  AND IFNULL(j.cleanup_notes, '') NOT IN ('manual user job')
		  AND (
			original_file.file_name LIKE 'tmp%%'
			OR vocal_file.file_name LIKE 'tmp%%'
			OR instrumental_file.file_name LIKE 'tmp%%'
			OR IFNULL(j.original_filename, '') LIKE 'tmp%%'
		  )
		"""
	)


def get_test_file_names() -> list[str]:
	p1, p2 = _test_user_patterns()
	return frappe.db.sql_list(
		"""
		SELECT name FROM tabFile
		WHERE file_name LIKE %s
		   OR file_name LIKE %s
		   OR owner LIKE %s
		   OR owner LIKE %s
		   OR file_name LIKE %s
		   OR (file_name = 'sample.mp3' AND owner LIKE %s)
		""",
		(
			"test_audio_stem_%",
			"tmp%%",
			p1,
			p2,
			AUDIO_USER_FILE_PATTERN,
			p1,
		),
	)


def get_test_user_names() -> list[str]:
	p1, p2 = _test_user_patterns()
	return frappe.db.sql_list(
		"""
		SELECT name FROM tabUser
		WHERE email LIKE %s OR email LIKE %s
		""",
		(p1, p2),
	)


def cleanup_legacy_audio_stem_test_data() -> dict:
	frappe.set_user("Administrator")
	p1, p2 = _test_user_patterns()

	result = dict(cleanup_audio_stem_test_data())

	user_job_names = set(get_test_job_names())
	empty_draft_names = set(get_empty_draft_test_job_names())
	tempfile_job_names = set(get_tempfile_test_job_names())
	job_names = list(user_job_names | empty_draft_names | tempfile_job_names)

	audit_before = frappe.db.count("Audio Stem Audit Log")
	if job_names:
		frappe.db.delete(
			"Audio Stem Audit Log",
			{
				"reference_name": ["in", job_names],
				"reference_doctype": "Audio Separation Job",
			},
		)
	frappe.db.sql(
		"""
		DELETE FROM `tabAudio Stem Audit Log`
		WHERE user LIKE %s OR user LIKE %s OR message LIKE %s
		""",
		(p1, p2, f"%{TEST_AUDIO_STEM_MARKER}%"),
	)
	result["audit_logs_removed"] = result.get("audit_logs_removed", 0) + (
		audit_before - frappe.db.count("Audio Stem Audit Log")
	)

	jobs_removed = 0
	audio_user_jobs_removed = 0
	empty_draft_jobs_removed = 0
	tempfile_jobs_removed = 0
	for name in job_names:
		if frappe.db.exists("Audio Separation Job", name):
			frappe.delete_doc("Audio Separation Job", name, force=True, ignore_permissions=True)
			jobs_removed += 1
			if name in user_job_names:
				audio_user_jobs_removed += 1
			if name in empty_draft_names:
				empty_draft_jobs_removed += 1
			if name in tempfile_job_names:
				tempfile_jobs_removed += 1
	result["legacy_jobs_removed"] = jobs_removed
	result["legacy_audio_user_jobs_removed"] = audio_user_jobs_removed
	result["empty_draft_jobs_removed"] = empty_draft_jobs_removed
	result["tempfile_jobs_removed"] = tempfile_jobs_removed

	files_removed = 0
	for name in get_test_file_names():
		if frappe.db.exists("File", name):
			frappe.delete_doc("File", name, force=True, ignore_permissions=True)
			files_removed += 1
	result["legacy_files_removed"] = files_removed

	users_removed = 0
	skipped_users: list[str] = []
	for name in get_test_user_names():
		if name in ("Administrator", "Guest"):
			continue
		if not frappe.db.exists("User", name):
			continue
		try:
			frappe.delete_doc("User", name, force=True, ignore_permissions=True)
			users_removed += 1
		except Exception:
			skipped_users.append(name)
	result["legacy_users_removed"] = users_removed
	result["skipped_users"] = skipped_users

	frappe.db.commit()
	return result


if __name__ == "__main__":
	print(cleanup_legacy_audio_stem_test_data())
