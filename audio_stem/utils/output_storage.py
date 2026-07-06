# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
from urllib.parse import urlparse

import frappe
import requests
from frappe import _
from frappe.utils import cint

from audio_stem.utils.limits import get_settings


def maybe_store_outputs_locally(job, vocal_url: str, instrumental_url: str) -> str | None:
	settings = get_settings()
	if not cint(settings.store_outputs_locally):
		return None

	warnings = []
	vocal_file = _download_and_attach(job, vocal_url, "vocal")
	if vocal_file:
		job.vocal_file = vocal_file
	else:
		warnings.append(_("Vocal output could not be saved locally."))

	instrumental_file = _download_and_attach(job, instrumental_url, "instrumental")
	if instrumental_file:
		job.instrumental_file = instrumental_file
	else:
		warnings.append(_("Instrumental output could not be saved locally."))

	if not warnings:
		return None
	return " ".join(warnings)


def _download_and_attach(job, url: str, stem_type: str) -> str | None:
	if not url:
		return None

	try:
		response = requests.get(url, timeout=120)
		response.raise_for_status()
		content = response.content
		if not content:
			return None

		extension = _guess_extension(url, response.headers.get("Content-Type"))
		file_name = f"{job.name}-{stem_type}{extension}"
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"attached_to_doctype": job.doctype,
				"attached_to_name": job.name,
				"is_private": 1,
				"content": content,
			}
		)
		file_doc.save(ignore_permissions=True)
		return file_doc.file_url
	except Exception:
		frappe.log_error(title=f"Local output storage failed for {job.name} ({stem_type})")
		return None


def _guess_extension(url: str, content_type: str | None) -> str:
	path = urlparse(url).path
	_, ext = os.path.splitext(path)
	if ext:
		return ext

	content_type = (content_type or "").split(";")[0].strip().lower()
	return {
		"audio/mpeg": ".mp3",
		"audio/wav": ".wav",
		"audio/x-wav": ".wav",
		"audio/flac": ".flac",
		"audio/ogg": ".ogg",
		"audio/mp4": ".m4a",
	}.get(content_type, ".mp3")
