# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
from typing import NamedTuple

import frappe
from frappe import _

PROVIDER_MODEL = "wavespeed-ai/audio-vocal-isolator"


class SeparationResult(NamedTuple):
	vocal_url: str
	instrumental_url: str


def _get_api_key() -> str:
	settings = frappe.get_doc("Audio Separation Settings", "Audio Separation Settings")
	api_key = (settings.get_password("wavespeed_api_key", raise_exception=False) or "").strip()
	if not api_key:
		frappe.throw(_("WaveSpeed API key is not configured in Audio Separation Settings."))
	return api_key


def isolate_vocal_and_instrumental(local_audio_path: str) -> SeparationResult:
	settings = frappe.get_single("Audio Separation Settings")
	if not settings.enabled:
		frappe.throw(_("Audio separation is disabled in Audio Separation Settings."))

	api_key = _get_api_key()

	import wavespeed

	os.environ["WAVESPEED_API_KEY"] = api_key
	client = wavespeed.Client(api_key=api_key)

	uploaded_url = client.upload(local_audio_path)
	output = client.run(
		PROVIDER_MODEL,
		{"audio": uploaded_url},
	)

	outputs = output.get("outputs") if isinstance(output, dict) else None
	if not outputs or len(outputs) < 2:
		frappe.throw(_("WaveSpeed did not return vocal and instrumental outputs."))

	return SeparationResult(vocal_url=outputs[0], instrumental_url=outputs[1])
