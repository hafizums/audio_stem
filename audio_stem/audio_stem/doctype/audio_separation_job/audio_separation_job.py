# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe.model.document import Document


class AudioSeparationJob(Document):
	def before_insert(self):
		if not self.user:
			self.user = frappe.session.user
