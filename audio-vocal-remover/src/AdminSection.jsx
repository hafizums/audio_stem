import { useState } from "react";
import { useFrappeGetCall } from "frappe-react-sdk";
import AdminChecklist from "./AdminChecklist";
import { formatDateTime, parseFrappeError, unwrapFrappeMessage } from "./utils";

const ADMIN_TABS = [
	{ key: "checklist", label: "Checklist" },
	{ key: "queue", label: "Queue" },
	{ key: "provider", label: "Provider" },
	{ key: "usage", label: "Usage" },
];

function HealthCard({ title, children }) {
	return (
		<div className="rounded-md border border-gray-200 bg-gray-50 p-3 text-sm">
			<p className="font-medium text-gray-900">{title}</p>
			<div className="mt-2 space-y-1 text-gray-700">{children}</div>
		</div>
	);
}

export default function AdminSection() {
	const [activeTab, setActiveTab] = useState("checklist");

	const { data: checklistResponse, error: checklistError, isLoading } = useFrappeGetCall(
		"audio_stem.api.admin.get_configuration_checklist"
	);
	const { data: queueResponse } = useFrappeGetCall("audio_stem.api.admin.get_queue_health");
	const { data: providerResponse } = useFrappeGetCall("audio_stem.api.admin.get_provider_health");
	const { data: usageResponse } = useFrappeGetCall("audio_stem.api.admin.get_audio_stem_usage_summary");

	const checklistItems = unwrapFrappeMessage(checklistResponse) || [];
	const queue = unwrapFrappeMessage(queueResponse);
	const provider = unwrapFrappeMessage(providerResponse);
	const usage = unwrapFrappeMessage(usageResponse);

	return (
		<div className="space-y-4">
			<div className="overflow-x-auto rounded-lg border border-gray-200 bg-white p-1">
				<nav className="flex min-w-max gap-1" aria-label="Admin sections">
					{ADMIN_TABS.map((tab) => (
						<button
							key={tab.key}
							type="button"
							onClick={() => setActiveTab(tab.key)}
							className={`whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium ${
								activeTab === tab.key
									? "bg-gray-900 text-white"
									: "text-gray-700 hover:bg-gray-100"
							}`}
						>
							{tab.label}
						</button>
					))}
				</nav>
			</div>

			{activeTab === "checklist" && (
				<section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
					<h3 className="mb-3 text-sm font-semibold text-gray-900">Configuration checklist</h3>
					<p className="mb-3 text-xs text-gray-500">
						System Manager view. No secrets are shown here.
					</p>
					<AdminChecklist
						items={checklistItems}
						loading={isLoading}
						error={
							checklistError
								? parseFrappeError(checklistError) || "Unable to load checklist."
								: null
						}
					/>
				</section>
			)}

			{activeTab === "queue" && (
				<section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
					<h3 className="mb-3 text-sm font-semibold text-gray-900">Queue health</h3>
					{queue ? (
						<div className="grid gap-3 sm:grid-cols-2">
							<HealthCard title="Queue summary">
								<p>Active: {queue.active_jobs_count}</p>
								<p>Queued: {queue.queued_jobs_count}</p>
								<p>Uploading: {queue.uploading_jobs_count}</p>
								<p>Processing: {queue.processing_jobs_count}</p>
								<p>Recent failures (24h): {queue.recent_failures_count}</p>
							</HealthCard>
							<HealthCard title="Guidance">
								<p>{queue.worker_guidance_message}</p>
								{queue.oldest_active_job_age_minutes != null && (
									<p>Oldest active job: {queue.oldest_active_job_age_minutes} min</p>
								)}
							</HealthCard>
						</div>
					) : (
						<p className="text-sm text-gray-500">Loading queue health…</p>
					)}
					{queue?.stuck_jobs?.length > 0 && (
						<div className="mt-4 overflow-x-auto">
							<table className="min-w-full border border-gray-200 text-sm">
								<thead className="bg-gray-50">
									<tr>
										<th className="border-b px-2 py-2 text-left">Job</th>
										<th className="border-b px-2 py-2 text-left">User</th>
										<th className="border-b px-2 py-2 text-left">Status</th>
										<th className="border-b px-2 py-2 text-left">Age (min)</th>
										<th className="border-b px-2 py-2 text-left">Created</th>
									</tr>
								</thead>
								<tbody>
									{queue.stuck_jobs.map((row) => (
										<tr key={row.name}>
											<td className="border-b px-2 py-2">{row.name}</td>
											<td className="border-b px-2 py-2">{row.user}</td>
											<td className="border-b px-2 py-2">{row.status}</td>
											<td className="border-b px-2 py-2">{row.age_minutes}</td>
											<td className="border-b px-2 py-2">{formatDateTime(row.creation)}</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					)}
				</section>
			)}

			{activeTab === "provider" && (
				<section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
					<h3 className="mb-3 text-sm font-semibold text-gray-900">Provider health</h3>
					{provider ? (
						<div className="rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700">
							<p>
								Status: <strong>{provider.status}</strong>
							</p>
							<p>{provider.message}</p>
							<p>Completed (24h): {provider.completed_count}</p>
							<p>Failed (24h): {provider.failed_count}</p>
							{provider.success_rate != null && <p>Success rate: {provider.success_rate}</p>}
							<p>Transcription completed (24h): {provider.transcription_completed_count}</p>
							<p>Karaoke ASS completed (24h): {provider.karaoke_ass_completed_count}</p>
							<p>Karaoke ASS failed (24h): {provider.karaoke_ass_failed_count}</p>
							<p>Karaoke video completed (24h): {provider.karaoke_video_completed_count}</p>
							<p>Karaoke video failed (24h): {provider.karaoke_video_failed_count}</p>
						</div>
					) : (
						<p className="text-sm text-gray-500">Loading provider health…</p>
					)}
				</section>
			)}

			{activeTab === "usage" && (
				<section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
					<h3 className="mb-3 text-sm font-semibold text-gray-900">Usage summary</h3>
					{usage ? (
						<div className="rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700">
							<p>Total jobs: {usage.total_jobs}</p>
							<p>Completed: {usage.completed_jobs}</p>
							<p>Failed: {usage.failed_jobs}</p>
							<p>Total duration: {usage.total_duration_seconds}s</p>
							<p>Total provider cost: {usage.total_provider_cost_usd}</p>
						</div>
					) : (
						<p className="text-sm text-gray-500">Loading usage summary…</p>
					)}
				</section>
			)}
		</div>
	);
}
