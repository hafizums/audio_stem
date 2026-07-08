# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Safe test data tagging, settings snapshot/restore, and cleanup helpers."""

from __future__ import annotations

import os
import tempfile
import time
from contextlib import contextmanager

import frappe
from frappe.exceptions import QueryTimeoutError

SETTINGS_DOCTYPE = "Audio Separation Settings"
TEST_AUDIO_STEM_MARKER = "TEST_AUDIO_STEM"
TEST_FILE_PREFIX = "test_audio_stem_"
TEST_USER_EMAIL_SUFFIX = "@audio-stem-test.example"

PASSWORD_FIELDS = frozenset({"wavespeed_api_key", "openai_api_key"})
SKIP_RESTORE_FIELDS = frozenset(
	{
		"name",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"docstatus",
		"idx",
	}
)


def is_test_file_name(file_name: str | None) -> bool:
	if not file_name:
		return False
	base = os.path.basename(file_name)
	return base.startswith(TEST_FILE_PREFIX) or TEST_AUDIO_STEM_MARKER in base


def is_test_job_name(job_name: str | None) -> bool:
	if not job_name:
		return False
	return bool(
		frappe.db.exists(
			"Audio Separation Job",
			{
				"name": job_name,
				"cleanup_notes": TEST_AUDIO_STEM_MARKER,
			},
		)
		or frappe.db.exists(
			"Audio Separation Job",
			{
				"name": job_name,
				"original_filename": ["like", f"{TEST_FILE_PREFIX}%"],
			},
		)
	)


def snapshot_audio_settings() -> dict:
	"""Capture current Audio Separation Settings for later restore."""
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	snapshot: dict = {}
	for fieldname in settings.meta.get_valid_columns():
		if fieldname in SKIP_RESTORE_FIELDS:
			continue
		if fieldname in PASSWORD_FIELDS:
			snapshot[fieldname] = settings.get_password(fieldname, raise_exception=False)
		else:
			snapshot[fieldname] = settings.get(fieldname)
	return snapshot


def restore_audio_settings(snapshot: dict | None) -> None:
	"""Restore Audio Separation Settings from a prior snapshot."""
	if not snapshot:
		return

	settings = frappe.get_single(SETTINGS_DOCTYPE)
	valid_columns = set(settings.meta.get_valid_columns())
	for fieldname, value in snapshot.items():
		if fieldname not in valid_columns or fieldname in SKIP_RESTORE_FIELDS:
			continue
		if fieldname in PASSWORD_FIELDS:
			setattr(settings, fieldname, value or "")
			continue
		settings.set(fieldname, value)

	settings.save(ignore_permissions=True)
	frappe.clear_cache(doctype=SETTINGS_DOCTYPE)


@contextmanager
def temporary_audio_settings(**overrides):
	"""Apply temporary settings for a block and restore the prior snapshot on exit."""
	from audio_stem.tests.base import DEFAULT_AUDIO_SEPARATION_TEST_SETTINGS

	snapshot = snapshot_audio_settings()
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	merged = dict(DEFAULT_AUDIO_SEPARATION_TEST_SETTINGS)
	merged.update(overrides)
	valid_columns = set(settings.meta.get_valid_columns())
	for fieldname, value in merged.items():
		if fieldname in valid_columns:
			settings.set(fieldname, value)
	settings.save(ignore_permissions=True)
	try:
		yield settings
	finally:
		restore_audio_settings(snapshot)


def _test_job_names() -> list[str]:
	names = frappe.get_all(
		"Audio Separation Job",
		filters={"cleanup_notes": TEST_AUDIO_STEM_MARKER},
		pluck="name",
	)
	names += frappe.get_all(
		"Audio Separation Job",
		filters={"original_filename": ["like", f"{TEST_FILE_PREFIX}%"]},
		pluck="name",
	)
	return list(dict.fromkeys(names))


def _safe_delete_doc(doctype: str, name: str, *, retries: int = 3, delay_seconds: float = 0.1) -> bool:
	"""Delete a document with retry to avoid lock-timeout flakes during tests."""
	for attempt in range(retries):
		try:
			if not frappe.db.exists(doctype, name):
				return False
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
			return True
		except QueryTimeoutError:
			if attempt + 1 >= retries:
				raise
			frappe.db.rollback()
			time.sleep(delay_seconds * (attempt + 1))
	return False


def cleanup_test_files() -> int:
	"""Delete only files clearly created by audio_stem tests."""
	removed = 0
	file_names = frappe.get_all(
		"File",
		filters={"file_name": ["like", f"{TEST_FILE_PREFIX}%"]},
		pluck="name",
	)
	for name in file_names:
		if _safe_delete_doc("File", name):
			removed += 1
	return removed


def cleanup_test_audit_logs() -> int:
	"""Delete audit logs linked to test jobs (direct SQL; append-only DocType)."""
	job_names = _test_job_names()
	if not job_names:
		return 0
	before = frappe.db.count("Audio Stem Audit Log")
	frappe.db.delete(
		"Audio Stem Audit Log",
		{
			"reference_name": ["in", job_names],
			"reference_doctype": "Audio Separation Job",
		},
	)
	# Also remove audit rows explicitly tagged in metadata/message during tests.
	frappe.db.sql(
		"""
		DELETE FROM `tabAudio Stem Audit Log`
		WHERE message LIKE %s OR metadata_json LIKE %s
		""",
		(f"%{TEST_AUDIO_STEM_MARKER}%", f"%{TEST_AUDIO_STEM_MARKER}%"),
	)
	return max(before - frappe.db.count("Audio Stem Audit Log"), 0)


def cleanup_test_error_logs_if_safe() -> int:
	"""Delete Error Log rows that are clearly from audio_stem tests."""
	if not frappe.db.table_exists("tabError Log"):
		return 0
	before = frappe.db.count("Error Log")
	frappe.db.sql(
		"""
		DELETE FROM `tabError Log`
		WHERE method LIKE %s
		   OR error LIKE %s
		   OR title LIKE %s
		""",
		(
			f"%{TEST_FILE_PREFIX}%",
			f"%{TEST_AUDIO_STEM_MARKER}%",
			f"%{TEST_AUDIO_STEM_MARKER}%",
		),
	)
	return max(before - frappe.db.count("Error Log"), 0)


def cleanup_test_jobs() -> int:
	"""Delete only Audio Separation Jobs created by tests."""
	removed = 0
	for name in _test_job_names():
		if _safe_delete_doc("Audio Separation Job", name):
			removed += 1
	return removed


def cleanup_audio_stem_test_data() -> dict:
	"""Run all safe test cleanups. Returns counts for debugging."""
	frappe.set_user("Administrator")
	audit_removed = cleanup_test_audit_logs()
	jobs_removed = cleanup_test_jobs()
	files_removed = cleanup_test_files()
	error_logs_removed = cleanup_test_error_logs_if_safe()
	frappe.db.commit()
	return {
		"jobs_removed": jobs_removed,
		"files_removed": files_removed,
		"audit_logs_removed": audit_removed,
		"error_logs_removed": error_logs_removed,
	}


def create_test_file_doc(*, suffix: str = ".mp3", content: bytes = b"audio", label: str = "input"):
	"""Create a private File tagged as test data."""
	file_name = f"{TEST_FILE_PREFIX}{label}{suffix}"
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"is_private": 1,
			"content": content,
		}
	)
	file_doc.save(ignore_permissions=True)
	return file_doc


def create_test_file_from_temp(*, suffix: str = ".mp3", content: bytes = b"audio", label: str = "input"):
	"""Create a test file using temp path semantics used by older tests."""
	with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
		tmp.write(content)
		tmp_path = tmp.name
	file_doc = create_test_file_doc(suffix=suffix, content=content, label=label)
	os.unlink(tmp_path)
	return file_doc


def mark_test_job(job) -> None:
	job.cleanup_notes = TEST_AUDIO_STEM_MARKER
	if not (job.original_filename or "").startswith(TEST_FILE_PREFIX):
		job.original_filename = f"{TEST_FILE_PREFIX}input.mp3"


def create_test_job_doc(*, user: str | None = None, with_outputs: bool = True, **fields):
	"""Create a tagged Audio Separation Job for tests."""
	user = user or frappe.session.user
	payload = {
		"doctype": "Audio Separation Job",
		"user": user,
		"status": fields.pop("status", "Completed"),
		"cleanup_notes": TEST_AUDIO_STEM_MARKER,
		"original_filename": fields.pop("original_filename", f"{TEST_FILE_PREFIX}input.mp3"),
		"duration_seconds": fields.pop("duration_seconds", 30),
	}
	if with_outputs:
		payload["original_file"] = fields.pop("original_file", create_test_file_doc(label="input").file_url)
		payload["vocal_file"] = fields.pop("vocal_file", create_test_file_doc(label="vocal", suffix=".mp3").file_url)
		payload["instrumental_file"] = fields.pop(
			"instrumental_file", create_test_file_doc(label="instrumental", suffix=".mp3").file_url
		)
	payload.update(fields)
	job = frappe.get_doc(payload)
	mark_test_job(job)
	job.insert(ignore_permissions=True)
	return job


def ensure_test_user(email: str) -> str:
	if TEST_USER_EMAIL_SUFFIX not in email:
		email = f"{email.split('@')[0]}{TEST_USER_EMAIL_SUFFIX}"
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
	return email
