# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe.model.document import Document

from audio_stem.utils.karaoke_style_settings import validate_job_karaoke_style_overrides


class AudioSeparationJob(Document):
	def before_insert(self):
		if not self.user:
			self.user = frappe.session.user

	def validate(self):
		validate_job_karaoke_style_overrides(self)
