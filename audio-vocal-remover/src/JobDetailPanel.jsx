import {
	ACTIVE_STATUSES,
	formatCost,
	formatDateTime,
	getJobStatusMessage,
	getStatusBadgeClass,
} from "./utils";

function StatusBadge({ status }) {
	return (
		<span
			className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${getStatusBadgeClass(status)}`}
		>
			{status}
		</span>
	);
}

function DetailRow({ label, children }) {
	return (
		<div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
			<dt className="min-w-32 text-sm font-medium text-gray-500">{label}</dt>
			<dd className="text-sm text-gray-900">{children}</dd>
		</div>
	);
}

function AudioPreview({ label, src, downloadLabel }) {
	if (!src) {
		return (
			<div>
				<p className="mb-2 text-sm font-medium text-gray-700">{label}</p>
				<p className="text-sm text-gray-500">Not available.</p>
			</div>
		);
	}

	return (
		<div>
			<p className="mb-2 text-sm font-medium text-gray-700">{label}</p>
			<audio controls preload="none" src={src} className="w-full max-w-full" />
			<a
				href={src}
				className="mt-2 inline-block text-sm text-blue-600 hover:underline"
				download
			>
				{downloadLabel}
			</a>
		</div>
	);
}

export default function JobDetailPanel({
	job,
	settings,
	displayCurrency,
	statusMessage,
	onRetry,
	onZip,
	retrying,
	zipping,
}) {
	if (!job) {
		return (
			<div className="rounded-lg border border-dashed border-gray-300 bg-white p-6 text-center">
				<p className="text-sm text-gray-500">
					Select a job from Recent Jobs or upload audio to inspect job details here.
				</p>
			</div>
		);
	}

	const vocalSrc = job.vocal_output_url || job.vocal_file;
	const instrumentalSrc = job.instrumental_output_url || job.instrumental_file;
	const isProgress = ACTIVE_STATUSES.includes(job.status);

	return (
		<div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
			<div className="flex flex-wrap items-start justify-between gap-2">
				<div>
					<h2 className="text-base font-semibold text-gray-900">Job Detail</h2>
					<p className="mt-1 text-sm text-gray-600">{job.name}</p>
				</div>
				<StatusBadge status={job.status} />
			</div>

			{isProgress && (
				<div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800">
					{statusMessage}
				</div>
			)}

			{job.status === "Completed" && (
				<div className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
					Separation completed successfully. Preview or download your tracks below.
				</div>
			)}

			{job.status === "Failed" && (
				<div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
					<p>{job.error_message || "Separation failed."}</p>
					{job.can_retry && (
						<p className="mt-1">You can retry this job if credits and limits allow.</p>
					)}
				</div>
			)}

			<dl className="grid gap-3">
				<DetailRow label="Original file">{job.original_filename || "—"}</DetailRow>
				<DetailRow label="Duration">
					{job.duration_seconds ? `${job.duration_seconds}s` : "Unknown"}
				</DetailRow>
				<DetailRow label="Provider cost">
					{job.provider_cost_usd
						? formatCost(job.provider_cost_usd, displayCurrency)
						: "—"}
				</DetailRow>
				<DetailRow label="Created">{formatDateTime(job.creation) || "—"}</DetailRow>
				<DetailRow label="Started">{formatDateTime(job.started_at) || "—"}</DetailRow>
				<DetailRow label="Completed">{formatDateTime(job.completed_at) || "—"}</DetailRow>
				{settings?.credit_management_enabled && (
					<DetailRow label="Credit status">{job.credit_status || "—"}</DetailRow>
				)}
				{settings?.credit_management_enabled && job.credit_error && (
					<DetailRow label="Credit error">
						<span className="text-red-600">{job.credit_error}</span>
					</DetailRow>
				)}
				{job.cleanup_notes && (
					<DetailRow label="Cleanup note">
						<span className="text-gray-600">{job.cleanup_notes}</span>
					</DetailRow>
				)}
			</dl>

			<div className="flex flex-wrap gap-2">
				{job.status === "Failed" && job.can_retry && (
					<button
						type="button"
						disabled={retrying || job.is_active}
						onClick={() => onRetry(job.name)}
						className="rounded-md bg-amber-600 px-3 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-50"
					>
						{retrying ? "Retrying..." : "Retry Job"}
					</button>
				)}
				{job.can_zip && (
					<button
						type="button"
						disabled={zipping}
						onClick={() => onZip(job.name)}
						className="rounded-md bg-green-600 px-3 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50"
					>
						{zipping ? "Creating ZIP..." : "Download ZIP"}
					</button>
				)}
			</div>

			<div className="grid gap-4 border-t border-gray-100 pt-4 md:grid-cols-1">
				<AudioPreview
					label="Original audio"
					src={job.original_file}
					downloadLabel="Download original"
				/>
				<AudioPreview label="Vocal track" src={vocalSrc} downloadLabel="Download vocal" />
				<AudioPreview
					label="Instrumental track"
					src={instrumentalSrc}
					downloadLabel="Download instrumental"
				/>
			</div>
		</div>
	);
}

export { StatusBadge };
