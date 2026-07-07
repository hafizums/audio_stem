# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
import tempfile
import traceback
import zipfile

import frappe
import requests
from frappe import _

from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.files import is_external_file_url, resolve_frappe_file_path
from audio_stem.utils.output_storage import _guess_extension


def create_job_zip_file(job) -> str:
	if job.status != "Completed":
		frappe.throw(_("ZIP download is only available for completed jobs."), frappe.ValidationError)

	existing_zip = _get_existing_zip_url(job)
	if existing_zip:
		return existing_zip

	temp_paths: list[str] = []
	vocal_path, vocal_name = _resolve_stem_path(job, "vocal", temp_paths)
	instrumental_path, instrumental_name = _resolve_stem_path(job, "instrumental", temp_paths)

	if not vocal_path or not instrumental_path:
		frappe.throw(
			_(
				"Vocal and instrumental outputs could not be found. "
				"The output files may have expired or been removed by cleanup."
			),
			frappe.ValidationError,
		)

	zip_name = f"{job.name}_stems.zip"
	temp_dir = tempfile.mkdtemp()
	temp_paths.append(temp_dir)
	zip_path = os.path.join(temp_dir, zip_name)

	try:
		with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
			archive.write(vocal_path, arcname=vocal_name)
			archive.write(instrumental_path, arcname=instrumental_name)

		with open(zip_path, "rb") as handle:
			content = handle.read()

		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": zip_name,
				"attached_to_doctype": job.doctype,
				"attached_to_name": job.name,
				"is_private": 1,
				"content": content,
			}
		)
		file_doc.save(ignore_permissions=True)
		return file_doc.file_url
	except Exception as exc:
		frappe.log_error(title=f"ZIP creation failed for {job.name}", message=traceback.format_exc())
		frappe.throw(safe_error_message(exc), frappe.ValidationError)
	finally:
		for path in reversed(temp_paths):
			if not path or not os.path.exists(path):
				continue
			if os.path.isdir(path):
				try:
					os.rmdir(path)
				except OSError:
					pass
			else:
				try:
					os.remove(path)
				except OSError:
					pass


def _get_existing_zip_url(job) -> str | None:
	if not job.zip_file:
		return None
	if resolve_frappe_file_path(job.zip_file):
		return job.zip_file
	return None


def _get_local_file_path(file_url: str) -> str | None:
	return resolve_frappe_file_path(file_url)


def _resolve_stem_path(job, stem_type: str, temp_paths: list[str]) -> tuple[str | None, str | None]:
	local_field = f"{stem_type}_file"
	url_field = f"{stem_type}_output_url"
	archive_name = f"{job.name}_{stem_type}"

	for file_url in (getattr(job, local_field, None), getattr(job, url_field, None)):
		if not file_url:
			continue

		if is_external_file_url(file_url):
			downloaded = _download_temp_stem(file_url, archive_name, temp_paths)
			if downloaded[0]:
				return downloaded
			continue

		local_path = resolve_frappe_file_path(file_url)
		if local_path:
			_, ext = os.path.splitext(local_path)
			return local_path, f"{archive_name}{ext or '.mp3'}"

	return None, None


def _download_temp_stem(url: str, archive_name: str, temp_paths: list[str]) -> tuple[str | None, str | None]:
	temp_dir = tempfile.mkdtemp()
	temp_paths.append(temp_dir)
	try:
		response = requests.get(url, timeout=120)
		response.raise_for_status()
		content = response.content
		if not content:
			return None, None

		extension = _guess_extension(url, response.headers.get("Content-Type"))
		temp_path = os.path.join(temp_dir, f"{archive_name}{extension}")
		with open(temp_path, "wb") as handle:
			handle.write(content)
		temp_paths.append(temp_path)
		return temp_path, f"{archive_name}{extension}"
	except Exception:
		frappe.log_error(title=f"Failed to download stem for ZIP from external URL")
		return None, None
