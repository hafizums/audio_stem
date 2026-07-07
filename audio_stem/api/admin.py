# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe

from audio_stem.utils.usage import get_usage_summary


@frappe.whitelist()
def get_audio_stem_usage_summary():
	return get_usage_summary()
