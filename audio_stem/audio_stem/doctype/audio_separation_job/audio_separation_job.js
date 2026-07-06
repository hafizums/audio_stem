// Copyright (c) 2026, Hafiz and contributors
// License: MIT. See LICENSE

frappe.ui.form.on("Audio Separation Job", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		const can_start = ["Draft", "Failed"].includes(frm.doc.status);
		if (!can_start) {
			return;
		}

		frm.add_custom_button(__("Start Separation"), () => {
			frappe.call({
				method: "audio_stem.api.separation.start_separation",
				args: { job_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Starting separation..."),
				callback() {
					frm.reload_doc();
				},
			});
		});
	},
});
