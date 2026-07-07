# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _


class AudioStemAuditLog(frappe.model.document.Document):
	def validate(self):
		if not self.is_new():
			frappe.throw(_("Audio Stem Audit Log entries cannot be modified."))

	def on_trash(self):
		frappe.throw(_("Audio Stem Audit Log entries cannot be deleted."))
