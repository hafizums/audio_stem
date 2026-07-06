frappe.pages["audio-vocal-remover"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Audio Vocal Remover"),
		single_column: true,
	});

	frappe.breadcrumbs.add("Audio Stem");

	const root = $('<div class="audio-vocal-remover"></div>').appendTo(page.main);

	const state = {
		job_name: null,
		poll_timer: null,
		cost_per_second: null,
	};

	load_settings();
	render_page();
	load_recent_jobs();

	function load_settings() {
		frappe.call({
			method: "audio_stem.api.separation.get_page_settings",
			callback(r) {
				if (!r.message) return;
				state.cost_per_second = r.message.cost_per_second_usd || 0;
				state.enabled = r.message.enabled;
			},
		});
	}

	function render_page() {
		root.empty();

		root.append(section(__("Upload Audio"), "upload-section"));
		root.append(section(__("Cost Estimate"), "estimate-section"));
		root.append(section(__("Start Separation"), "start-section"));
		root.append(section(__("Job Status"), "status-section"));
		root.append(section(__("Original Audio"), "original-audio-section"));
		root.append(section(__("Vocal Output"), "vocal-section"));
		root.append(section(__("Instrumental Output"), "instrumental-section"));
		root.append(section(__("Recent Jobs"), "recent-jobs-section"));

		render_upload();
		render_estimate();
		render_start_button();
		render_status();
		render_audio_sections();
		render_recent_jobs_table();
	}

	function section(title, class_name) {
		return $(`<div class="avr-section ${class_name}">
			<h5>${title}</h5>
			<div class="avr-body"></div>
		</div>`);
	}

	function render_upload() {
		const body = root.find(".upload-section .avr-body");
		body.empty();

		if (state.job_name) {
			body.append(`<p class="avr-muted">${__("Job created. Upload a new file to start over.")}</p>`);
		}

		$(`<button class="btn btn-primary btn-sm">${__("Upload Audio File")}</button>`)
			.appendTo(body)
			.on("click", () => {
				new frappe.ui.FileUploader({
					allow_multiple: false,
					restrictions: {
						allowed_file_types: ["audio/*", ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"],
					},
					on_success(file) {
						create_job(file);
					},
				});
			});
	}

	function create_job(file) {
		frappe.call({
			method: "audio_stem.api.separation.create_job_from_file",
			args: { file_url: file.file_url },
			freeze: true,
			freeze_message: __("Creating job..."),
			callback(r) {
				if (!r.message) return;
				state.job_name = r.message.name;
				update_from_job(r.message);
				load_recent_jobs();
				frappe.show_alert({
					message: __("Job {0} created", [state.job_name]),
					indicator: "green",
				});
			},
		});
	}

	function render_estimate() {
		const body = root.find(".estimate-section .avr-body");
		body.empty();
		body.append(`<div class="estimate-text avr-muted">${__("Upload an audio file to see the estimate.")}</div>`);
	}

	function update_estimate(job) {
		const body = root.find(".estimate-section .avr-body .estimate-text");
		if (!job) {
			body.text(__("Upload an audio file to see the estimate."));
			return;
		}

		if (job.duration_seconds) {
			const cost = job.estimated_cost_usd ?? job.duration_seconds * (state.cost_per_second || 0);
			body.html(
				`${__("Duration")}: <strong>${job.duration_seconds}s</strong><br>` +
					`${__("Estimated provider cost")}: <strong>${format_currency(cost)}</strong>`
			);
			return;
		}

		body.text(__("Cost will be calculated after upload."));
	}

	function render_start_button() {
		const body = root.find(".start-section .avr-body");
		body.empty();

		const btn = $(`<button class="btn btn-primary" disabled>${__("Start Separation")}</button>`).appendTo(
			body
		);

		btn.on("click", () => {
			if (!state.job_name) return;

			if (state.enabled === 0) {
				frappe.msgprint(__("Audio separation is disabled in Audio Separation Settings."));
				return;
			}

			frappe.call({
				method: "audio_stem.api.separation.start_separation",
				args: { job_name: state.job_name },
				freeze: true,
				freeze_message: __("Starting separation..."),
				callback(r) {
					if (!r.message) return;
					poll_job_status();
				},
			});
		});

		state.start_button = btn;
		update_start_button();
	}

	function update_start_button() {
		if (!state.start_button) return;
		const enabled = Boolean(state.job_name);
		state.start_button.prop("disabled", !enabled);
	}

	function render_status() {
		const body = root.find(".status-section .avr-body");
		body.empty();
		body.append(`<div class="avr-status-badge">${__("No active job")}</div>`);
		body.append(`<div class="avr-error" style="display:none;"></div>`);
	}

	function update_status(job) {
		const body = root.find(".status-section .avr-body");
		const badge = body.find(".avr-status-badge");
		const error = body.find(".avr-error");

		if (!job) {
			badge.text(__("No active job"));
			error.hide();
			return;
		}

		badge.text(`${__("Status")}: ${job.status}`);

		if (job.status === "Failed" && job.error_message) {
			error.text(job.error_message).show();
		} else {
			error.hide();
		}
	}

	function render_audio_sections() {
		["original-audio-section", "vocal-section", "instrumental-section"].forEach((section_class) => {
			const body = root.find(`.${section_class} .avr-body`);
			body.empty();
			body.append(`<p class="avr-muted">${__("Not available yet.")}</p>`);
		});
	}

	function update_audio_sections(job) {
		render_player(
			"original-audio-section",
			job?.original_file,
			null,
			__("Original audio not available.")
		);
		render_player("vocal-section", job?.vocal_output_url, "vocal", __("Vocal output not available yet."));
		render_player(
			"instrumental-section",
			job?.instrumental_output_url,
			"instrumental",
			__("Instrumental output not available yet.")
		);
	}

	function render_player(section_class, url, download_key, empty_text) {
		const body = root.find(`.${section_class} .avr-body`);
		body.empty();

		if (!url) {
			body.append(`<p class="avr-muted">${empty_text}</p>`);
			return;
		}

		const audio = $(`<audio controls preload="none" src="${frappe.utils.escape_html(url)}"></audio>`);
		body.append(audio);

		const actions = $('<div class="avr-actions"></div>').appendTo(body);
		if (download_key) {
			$(`<a class="btn btn-default btn-sm" href="${frappe.utils.escape_html(url)}" target="_blank" rel="noopener noreferrer" download>
				${download_key === "vocal" ? __("Download Vocal") : __("Download Instrumental")}
			</a>`).appendTo(actions);
		}
	}

	function update_from_job(job) {
		update_estimate(job);
		update_status(job);
		update_audio_sections(job);
		update_start_button();

		if (job && ["Queued", "Uploading", "Processing"].includes(job.status)) {
			if (!state.poll_timer) {
				poll_job_status();
			}
		} else {
			stop_polling();
		}

		if (job && ["Completed", "Failed", "Cancelled"].includes(job.status)) {
			load_recent_jobs();
		}
	}

	function poll_job_status() {
		if (!state.job_name) return;
		stop_polling();
		fetch_job_status();
		state.poll_timer = setInterval(fetch_job_status, 3000);
	}

	function stop_polling() {
		if (state.poll_timer) {
			clearInterval(state.poll_timer);
			state.poll_timer = null;
		}
	}

	function fetch_job_status() {
		if (!state.job_name) return;

		frappe.call({
			method: "audio_stem.api.separation.get_job_status",
			args: { job_name: state.job_name },
			callback(r) {
				if (!r.message) return;
				update_from_job(r.message);

				if (!["Queued", "Uploading", "Processing"].includes(r.message.status)) {
					stop_polling();
				}
			},
		});
	}

	function render_recent_jobs_table() {
		const body = root.find(".recent-jobs-section .avr-body");
		body.empty();
		body.append('<div class="recent-jobs-table"></div>');
	}

	function load_recent_jobs() {
		frappe.call({
			method: "audio_stem.api.separation.get_recent_jobs",
			args: { limit: 10 },
			callback(r) {
				const rows = r.message || [];
				const container = root.find(".recent-jobs-table");
				if (!container.length) return;

				if (!rows.length) {
					container.html(`<p class="avr-muted">${__("No jobs yet.")}</p>`);
					return;
				}

				const table = $(`
					<table class="table table-bordered table-sm">
						<thead>
							<tr>
								<th>${__("Job")}</th>
								<th>${__("Status")}</th>
								<th>${__("Created")}</th>
								<th>${__("Duration (s)")}</th>
							</tr>
						</thead>
						<tbody></tbody>
					</table>
				`);

				const tbody = table.find("tbody");
				rows.forEach((row) => {
					tbody.append(`
						<tr class="recent-job-row" data-job-name="${frappe.utils.escape_html(row.name)}">
							<td><a href="#">${frappe.utils.escape_html(row.name)}</a></td>
							<td>${frappe.utils.escape_html(row.status)}</td>
							<td>${frappe.datetime.str_to_user(row.creation)}</td>
							<td>${row.duration_seconds || ""}</td>
						</tr>
					`);
				});

				container.html(table);

				container.find(".recent-job-row").on("click", function (e) {
					e.preventDefault();
					state.job_name = $(this).attr("data-job-name");
					fetch_job_status();
					update_start_button();
					frappe.show_alert({
						message: __("Loaded job {0}", [state.job_name]),
						indicator: "blue",
					});
				});
			},
		});
	}

	function format_currency(value) {
		return frappe.format(value, { fieldtype: "Currency" });
	}

	$(wrapper).on("pagehide", () => {
		stop_polling();
	});
};
