# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

from frappe.model.document import Document

from audio_stem.utils.karaoke_style_settings import validate_karaoke_style_settings


class AudioSeparationSettings(Document):
	def validate(self):
		validate_karaoke_style_settings(self)

