import frappe


def execute():
	if frappe.db.exists("Workspace", "Audio Stem"):
		workspace = frappe.get_doc("Workspace", "Audio Stem")
		changed = False
		for row in workspace.shortcuts:
			if row.label == "Audio Vocal Remover" and row.type != "URL":
				row.type = "URL"
				row.url = "/audio-vocal-remover"
				row.link_to = ""
				changed = True
		if changed:
			workspace.save(ignore_permissions=True)

	if frappe.db.exists("Page", "audio-vocal-remover"):
		frappe.delete_doc("Page", "audio-vocal-remover", force=1)
