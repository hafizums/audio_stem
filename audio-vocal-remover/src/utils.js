const ACTIVE_STATUSES = ["Queued", "Uploading", "Processing"];
const TERMINAL_STATUSES = ["Completed", "Failed", "Cancelled"];

const STATUS_BADGE_CLASSES = {
	Draft: "bg-gray-100 text-gray-800",
	Queued: "bg-amber-100 text-amber-800",
	Uploading: "bg-blue-100 text-blue-800",
	Processing: "bg-indigo-100 text-indigo-800",
	Rendering: "bg-purple-100 text-purple-800",
	Completed: "bg-green-100 text-green-800",
	Failed: "bg-red-100 text-red-800",
	Cancelled: "bg-gray-200 text-gray-700",
	"Not Started": "bg-gray-100 text-gray-700",
};

export function formatCost(value, currency = "MYR") {
	try {
		return new Intl.NumberFormat(undefined, {
			style: "currency",
			currency,
			minimumFractionDigits: 2,
		}).format(value || 0);
	} catch {
		return `${currency} ${Number(value || 0).toFixed(2)}`;
	}
}

export function flt(value) {
	return parseFloat(value) || 0;
}

export function formatDateTime(value) {
	if (!value) return "";
	try {
		return new Intl.DateTimeFormat(undefined, {
			dateStyle: "medium",
			timeStyle: "short",
		}).format(new Date(value));
	} catch {
		return String(value);
	}
}

export function getStatusBadgeClass(status) {
	return STATUS_BADGE_CLASSES[status] || "bg-gray-100 text-gray-800";
}

export function getJobStatusMessage(job, { starting, retrying, zipping } = {}) {
	if (!job) return "Upload an audio file to create a job.";

	if (starting) return "Starting separation and reserving credits if required...";
	if (retrying) return "Retrying failed job. Previous outputs stay available until the new run succeeds.";
	if (zipping) return "Creating ZIP archive with vocal and instrumental tracks...";

	switch (job.status) {
		case "Queued":
			return "Your job is waiting in the queue.";
		case "Uploading":
			return "Uploading audio to the processing service.";
		case "Processing":
			return "Separating vocals and instrumentals. This may take a few minutes.";
		case "Completed":
			return "Separation completed. Preview, download, or create a ZIP below.";
		case "Failed":
			return job.error_message || "Separation failed. You can retry if allowed.";
		case "Cancelled":
			return "This job was cancelled.";
		case "Draft":
			return "Job is ready. Review the estimate and start separation when ready.";
		default:
			return `Status: ${job.status}`;
	}
}

export function parseFrappeError(data) {
	if (!data) return "Request failed";

	if (typeof data === "string") return data;

	if (data._server_messages) {
		try {
			const messages = JSON.parse(data._server_messages);
			const parsed = JSON.parse(messages[0]);
			if (parsed.message) return parsed.message;
		} catch {
			// Fall through to message field.
		}
	}

	return data.message || "Request failed";
}

export function unwrapFrappeMessage(data) {
	if (data && typeof data === "object" && data.message !== undefined) {
		return data.message;
	}
	return data;
}

export async function uploadAudioFile(file) {
	const formData = new FormData();
	formData.append("file", file);
	formData.append("is_private", "1");

	const csrfToken = window.csrf_token && window.csrf_token !== "None" ? window.csrf_token : "";

	const response = await fetch("/api/method/audio_stem.api.separation.upload_audio_file", {
		method: "POST",
		headers: {
			"X-Frappe-CSRF-Token": csrfToken,
		},
		credentials: "include",
		body: formData,
	});

	const data = await response.json();
	if (!response.ok || data.exc) {
		throw new Error(parseFrappeError(data) || "Upload failed");
	}

	return data.message;
}

export function getEstimatedCost(job, costPerSecond) {
	if (!job?.duration_seconds) return null;
	return job.estimated_cost_usd ?? job.duration_seconds * (costPerSecond || 0);
}

export function isStartDisabled({ job, jobName, starting, enabled, credit, costPerSecond, settings }) {
	if (!jobName || starting || enabled === 0) return true;
	if (!job) return true;
	if (!job.can_start || job.is_active) return true;

	const daily = settings?.daily_usage;
	if (daily?.limits_enabled) {
		if (daily.jobs_remaining === 0) return true;
		if (
			job.duration_seconds &&
			daily.duration_seconds_remaining != null &&
			job.duration_seconds > daily.duration_seconds_remaining
		) {
			return true;
		}
		const cost = getEstimatedCost(job, costPerSecond);
		if (
			cost &&
			daily.cost_usd_remaining != null &&
			cost > daily.cost_usd_remaining
		) {
			return true;
		}
	}

	const cost = getEstimatedCost(job, costPerSecond);
	if (
		credit?.enabled &&
		!credit.error &&
		job.duration_seconds &&
		credit.available_balance !== null &&
		credit.available_balance !== undefined &&
		flt(cost) > flt(credit.available_balance)
	) {
		return true;
	}

	return false;
}

export function getStartBlockedReason({ job, credit, costPerSecond, settings }) {
	if (!job) return "";
	if (job.is_active) return "A separation job is already running for this job.";

	const cost = getEstimatedCost(job, costPerSecond);
	if (
		credit?.enabled &&
		credit.error
	) {
		return credit.error;
	}
	if (
		credit?.enabled &&
		!credit.error &&
		job.duration_seconds &&
		credit.available_balance !== null &&
		credit.available_balance !== undefined &&
		flt(cost) > flt(credit.available_balance)
	) {
		return "Insufficient available credits for this separation job.";
	}

	if (!job.duration_seconds && job.status === "Draft") {
		return "Audio duration is unknown. Separation cannot be started until duration is available.";
	}

	if (!job.can_start) {
		return job.start_blocked_reason || "Start is not available for this job.";
	}

	const daily = settings?.daily_usage;
	if (daily?.limits_enabled) {
		if (daily.jobs_remaining === 0) {
			return "Daily job limit reached.";
		}
		if (
			job.duration_seconds &&
			daily.duration_seconds_remaining != null &&
			job.duration_seconds > daily.duration_seconds_remaining
		) {
			return "Daily audio duration limit reached.";
		}
		const cost = getEstimatedCost(job, costPerSecond);
		if (
			cost &&
			daily.cost_usd_remaining != null &&
			cost > daily.cost_usd_remaining
		) {
			return "Daily provider cost limit reached.";
		}
	}

	if (settings?.enabled === 0) {
		return "Audio separation is disabled in Audio Separation Settings.";
	}

	return "";
}

export function getUploadErrorMessage(err, settings) {
	const message = parseFrappeError(err) || err?.message || "Failed to upload audio";
	if (/exceeds the maximum allowed size/i.test(message)) {
		return `File is too large. Maximum allowed size is ${settings?.max_file_size_mb || "?"} MB.`;
	}
	if (/exceeds the maximum allowed duration/i.test(message)) {
		return `Audio is too long. Maximum allowed duration is ${settings?.max_audio_duration_seconds || "?"} seconds.`;
	}
	return message;
}

export { ACTIVE_STATUSES, TERMINAL_STATUSES };
