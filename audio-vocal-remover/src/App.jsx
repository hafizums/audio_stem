import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
	FrappeProvider,
	useFrappeAuth,
	useFrappeFileUpload,
	useFrappeGetCall,
	useFrappePostCall,
} from "frappe-react-sdk";
import AdminSection from "./AdminSection";
import JobDetailPanel, { StatusBadge } from "./JobDetailPanel";
import { StatusPill, Card } from "./components/ui";
import {
	ACTIVE_STATUSES,
	TERMINAL_STATUSES,
	formatCost,
	formatDateTime,
	getEstimatedCost,
	getJobStatusMessage,
	getStartBlockedReason,
	getUploadErrorMessage,
	isStartDisabled,
	parseFrappeError,
	unwrapFrappeMessage,
} from "./utils";

function LoginPrompt() {
	return (
		<div className="mx-auto max-w-lg rounded-xl border border-amber-200 bg-amber-50 p-6 text-center">
			<h1 className="text-xl font-semibold text-gray-900">Audio Vocal Remover</h1>
			<p className="mt-2 text-sm text-gray-600">
				Please log in to upload audio and run separation jobs.
			</p>
			<a
				href="/login?redirect-to=/audio-vocal-remover"
				className="mt-4 inline-block rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800"
			>
				Log in
			</a>
		</div>
	);
}

function PilotBlockedView({ currentUser, settings }) {
	return (
		<div className="min-h-screen bg-gray-50">
			<header className="border-b border-gray-200 bg-white">
				<div className="mx-auto max-w-6xl px-4 py-4">
					<h1 className="text-lg font-bold text-gray-900">Audio Vocal Remover</h1>
					<p className="text-sm text-gray-500">{currentUser}</p>
				</div>
			</header>
			<main className="mx-auto max-w-6xl p-4">
				<div className="rounded-lg border border-amber-200 bg-amber-50 p-6 text-center">
					<h2 className="text-lg font-semibold text-gray-900">Pilot access required</h2>
					<p className="mt-2 text-sm text-gray-700">
						{settings?.blocked_reason ||
							"Audio separation is currently limited to pilot users. Please contact an administrator."}
					</p>
				</div>
			</main>
		</div>
	);
}

function UploadCard({
	settings,
	uploading,
	onFileChange,
	displayCurrency,
	credit,
	estimatedCost,
	job,
}) {
	return (
		<Card title="Upload audio">
			<ul className="mb-4 space-y-1 text-sm text-gray-600">
				<li>
					Supported formats: {settings?.accepted_file_types || "MP3, WAV, M4A, FLAC, OGG, AAC"}
				</li>
				<li>
					Max file size: <strong>{settings?.max_file_size_mb ?? "—"} MB</strong>. Max duration:{" "}
					<strong>{settings?.max_audio_duration_seconds ?? "—"}s</strong>.
				</li>
				{settings?.credit_management_enabled && (
					<li>Credits are required before starting separation.</li>
				)}
				{settings?.daily_usage?.limits_enabled && (
					<li>Daily job, duration, and cost limits apply.</li>
				)}
			</ul>

			<label className="flex w-full cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-gray-300 bg-gray-50 px-4 py-10 text-center hover:border-purple-400 hover:bg-purple-50">
				<span className="text-base font-semibold text-gray-800">
					{uploading ? "Uploading..." : "Tap or drop an audio file here"}
				</span>
				<span className="mt-1 text-xs text-gray-500">
					We will create a draft job and detect its duration automatically.
				</span>
				<input
					type="file"
					className="hidden"
					accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg,.aac"
					disabled={uploading}
					onChange={onFileChange}
				/>
			</label>

			{settings?.credit_management_enabled && (
				<div className="mt-4 rounded-md border border-indigo-100 bg-indigo-50 px-3 py-2 text-sm text-indigo-900">
					<p>
						Available credit:{" "}
						<strong>{formatCost(credit?.available_balance, displayCurrency)}</strong>
					</p>
					{credit?.error && <p className="mt-1 text-red-700">{credit.error}</p>}
				</div>
			)}
		</Card>
	);
}

function JobSidebarSummary({ job, settings, displayCurrency, estimatedCost, credit }) {
	if (!job) return null;
	return (
		<Card title="Job summary">
			<dl className="space-y-2 text-sm">
				<div className="flex items-baseline justify-between gap-3">
					<dt className="text-gray-500">Status</dt>
					<dd className="text-right">
						<StatusBadge status={job.status} />
					</dd>
				</div>
				<div className="flex items-baseline justify-between gap-3">
					<dt className="text-gray-500">Duration</dt>
					<dd className="text-right font-medium text-gray-900">
						{job.duration_seconds ? `${job.duration_seconds}s` : "—"}
					</dd>
				</div>
				<div className="flex items-baseline justify-between gap-3">
					<dt className="text-gray-500">Estimated cost</dt>
					<dd className="text-right font-medium text-gray-900">
						{formatCost(estimatedCost, displayCurrency)}
					</dd>
				</div>
				<div className="flex items-baseline justify-between gap-3">
					<dt className="text-gray-500">Created</dt>
					<dd className="text-right font-medium text-gray-900">
						{formatDateTime(job.creation) || "—"}
					</dd>
				</div>
				{settings?.credit_management_enabled && (
					<div className="flex items-baseline justify-between gap-3">
						<dt className="text-gray-500">Credit status</dt>
						<dd className="text-right font-medium text-gray-900">
							{job.credit_status || "—"}
						</dd>
					</div>
				)}
			</dl>

			{job.transcription_status && job.transcription_status !== "Not Started" && (
				<div className="mt-3 border-t border-gray-100 pt-3 text-sm">
					<p className="text-gray-500">Transcription</p>
					<p className="mt-1 font-medium text-gray-900">
						<StatusBadge status={job.transcription_status} />
					</p>
				</div>
			)}
			{job.karaoke_status && job.karaoke_status !== "Not Started" && (
				<div className="mt-3 border-t border-gray-100 pt-3 text-sm">
					<p className="text-gray-500">Karaoke</p>
					<p className="mt-1 font-medium text-gray-900">
						<StatusBadge status={job.karaoke_status} />
					</p>
				</div>
			)}

			{settings?.credit_management_enabled && credit?.error && (
				<p className="mt-3 text-xs text-red-600">{credit.error}</p>
			)}
		</Card>
	);
}

function DailyUsageCard({ dailyUsage, displayCurrency }) {
	if (!dailyUsage?.limits_enabled) return null;
	return (
		<Card title="Today's usage">
			<dl className="space-y-1 text-sm text-gray-700">
				<div className="flex justify-between">
					<dt>Jobs</dt>
					<dd className="font-medium">
						{dailyUsage.jobs_today}
						{dailyUsage.daily_job_limit_per_user > 0
							? ` / ${dailyUsage.daily_job_limit_per_user}`
							: ""}
					</dd>
				</div>
				<div className="flex justify-between">
					<dt>Duration</dt>
					<dd className="font-medium">
						{dailyUsage.duration_seconds_today}s
						{dailyUsage.daily_duration_limit_seconds_per_user > 0
							? ` / ${dailyUsage.daily_duration_limit_seconds_per_user}s`
							: ""}
					</dd>
				</div>
				<div className="flex justify-between">
					<dt>Est. cost</dt>
					<dd className="font-medium">
						{formatCost(dailyUsage.cost_usd_today, displayCurrency)}
					</dd>
				</div>
			</dl>
		</Card>
	);
}

function getRecentJobStep(row) {
	if (row.transcription_status === "Completed") {
		return row.karaoke_status === "Completed" ? "Karaoke done" : "Karaoke";
	}
	if (row.transcription_status && row.transcription_status !== "Not Started") {
		return "Transcribe";
	}
	if (row.status === "Completed") {
		return "Transcribe";
	}
	return "Separate";
}

function matchesRecentJobSearch(row, query) {
	if (!query) return true;
	const haystack = [
		row.original_filename,
		row.name,
		row.status,
		row.transcription_status,
		row.karaoke_status,
		row.credit_status,
		row.error_summary,
		getRecentJobStep(row),
	]
		.filter(Boolean)
		.join(" ")
		.toLowerCase();
	return haystack.includes(query);
}

function RecentJobsTable({
	recentJobs,
	jobName,
	onOpen,
	onCancel,
	onRetry,
	onZip,
	cancelling,
	retrying,
	zipping,
	settings,
}) {
	const [searchQuery, setSearchQuery] = useState("");
	const normalizedQuery = searchQuery.trim().toLowerCase();

	const filteredJobs = useMemo(
		() => recentJobs.filter((row) => matchesRecentJobSearch(row, normalizedQuery)),
		[recentJobs, normalizedQuery]
	);

	if (!recentJobs?.length) {
		return (
			<div className="rounded-xl border border-dashed border-gray-300 bg-white px-4 py-10 text-center">
				<p className="text-sm font-medium text-gray-700">No completed jobs yet</p>
				<p className="mt-1 text-sm text-gray-500">
					Finish a separation job to see it listed here.
				</p>
			</div>
		);
	}

	return (
		<div className="space-y-3">
			<div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
				<label className="relative block min-w-0 flex-1 sm:max-w-md">
					<span className="sr-only">Search recent jobs</span>
					<input
						type="search"
						value={searchQuery}
						onChange={(event) => setSearchQuery(event.target.value)}
						placeholder="Search by filename, job ID, or status…"
						className="w-full rounded-lg border border-gray-300 bg-white py-2 pl-3 pr-9 text-sm text-gray-900 placeholder:text-gray-400 focus:border-purple-400 focus:outline-none focus:ring-2 focus:ring-purple-100"
					/>
					{searchQuery && (
						<button
							type="button"
							onClick={() => setSearchQuery("")}
							className="absolute right-2 top-1/2 -translate-y-1/2 rounded px-1.5 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-700"
							aria-label="Clear search"
						>
							Clear
						</button>
					)}
				</label>
				{normalizedQuery ? (
					<p className="text-xs text-gray-500 sm:shrink-0">
						Showing {filteredJobs.length} of {recentJobs.length}
					</p>
				) : (
					<p className="text-xs text-gray-500 sm:shrink-0">{recentJobs.length} completed</p>
				)}
			</div>

			{filteredJobs.length === 0 ? (
				<div className="rounded-xl border border-dashed border-gray-300 bg-white px-4 py-10 text-center">
					<p className="text-sm font-medium text-gray-700">No matching jobs</p>
					<p className="mt-1 text-sm text-gray-500">
						Try a different filename, job ID, or status.
					</p>
				</div>
			) : (
		<div className="max-h-[32rem] overflow-x-auto overflow-y-auto rounded-xl border border-gray-200 bg-white shadow-sm">
			<table className="min-w-full text-sm">
				<thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
					<tr>
						<th className="px-3 py-2">File</th>
						<th className="px-3 py-2">Status</th>
						<th className="hidden px-3 py-2 sm:table-cell">Step</th>
						<th className="hidden px-3 py-2 md:table-cell">Created</th>
						<th className="hidden px-3 py-2 lg:table-cell">Duration</th>
						<th className="px-3 py-2 text-right">Actions</th>
					</tr>
				</thead>
				<tbody className="divide-y divide-gray-100">
					{filteredJobs.map((row) => {
						const step = getRecentJobStep(row);
						return (
							<tr
								key={row.name}
								className={`hover:bg-gray-50 ${jobName === row.name ? "bg-purple-50" : ""}`}
							>
								<td className="px-3 py-2">
									<div className="font-medium text-gray-900">
										{row.original_filename || row.name}
									</div>
									<div className="text-xs text-gray-500">{row.name}</div>
								</td>
								<td className="px-3 py-2">
									<StatusBadge status={row.status} />
								</td>
								<td className="hidden px-3 py-2 text-gray-700 sm:table-cell">{step}</td>
								<td className="hidden px-3 py-2 text-gray-700 md:table-cell">
									{formatDateTime(row.creation) || "—"}
								</td>
								<td className="hidden px-3 py-2 text-gray-700 lg:table-cell">
									{row.duration_seconds ? `${row.duration_seconds}s` : "—"}
								</td>
								<td className="px-3 py-2">
									<div className="flex flex-wrap justify-end gap-2">
										<button
											type="button"
											onClick={() => onOpen(row.name)}
											className="text-sm text-blue-600 hover:underline"
										>
											View
										</button>
										{row.can_cancel && (
											<button
												type="button"
												disabled={cancelling}
												onClick={() => onCancel(row.name)}
												className="text-sm text-gray-700 hover:underline disabled:opacity-50"
											>
												Cancel
											</button>
										)}
										{row.can_retry && (
											<button
												type="button"
												disabled={retrying}
												onClick={() => onRetry(row.name)}
												className="text-sm text-amber-700 hover:underline disabled:opacity-50"
											>
												Retry
											</button>
										)}
										{row.can_zip && (
											<button
												type="button"
												disabled={zipping}
												onClick={() => onZip(row.name)}
												className="text-sm text-green-700 hover:underline disabled:opacity-50"
											>
												ZIP
											</button>
										)}
									</div>
								</td>
							</tr>
						);
					})}
				</tbody>
			</table>
		</div>
			)}
		</div>
	);
}

function AdminToolsPanel() {
	const [open, setOpen] = useState(false);
	return (
		<div className="rounded-xl border border-gray-200 bg-white shadow-sm">
			<button
				type="button"
				onClick={() => setOpen((v) => !v)}
				className="flex w-full items-center justify-between px-5 py-4 text-left"
				aria-expanded={open}
			>
				<div>
					<p className="text-sm font-semibold text-gray-900">Admin tools</p>
					<p className="text-xs text-gray-500">
						System Manager only. Collapsed by default.
					</p>
				</div>
				<span className="text-sm text-gray-500">{open ? "Hide" : "Show"}</span>
			</button>
			{open && (
				<div className="border-t border-gray-100 p-5">
					<AdminSection />
				</div>
			)}
		</div>
	);
}

function AudioStemWorkspace({ currentUser, settings: initialSettings }) {
	const [jobName, setJobName] = useState(null);
	const [job, setJob] = useState(null);
	const [starting, setStarting] = useState(false);
	const [retrying, setRetrying] = useState(false);
	const [zipping, setZipping] = useState(false);
	const [cancelling, setCancelling] = useState(false);
	const [transcribing, setTranscribing] = useState(false);
	const [karaokeRendering, setKaraokeRendering] = useState(false);
	const [savingJobKaraokeStyle, setSavingJobKaraokeStyle] = useState(false);
	const [savingSiteKaraokeStyle, setSavingSiteKaraokeStyle] = useState(false);
	const [jobKaraokeStyle, setJobKaraokeStyle] = useState(null);
	const [uploading, setUploading] = useState(false);
	const [error, setError] = useState(null);
	const pollRef = useRef(null);

	const { data: settingsResponse, mutate: refreshSettings } = useFrappeGetCall(
		"audio_stem.api.separation.get_page_settings"
	);
	const { data: creditBalanceResponse, mutate: refreshCredit } = useFrappeGetCall(
		"audio_stem.api.separation.get_my_credit_balance"
	);
	const { data: recentJobsResponse, mutate: refreshRecent } = useFrappeGetCall(
		"audio_stem.api.separation.get_recent_jobs",
		{ limit: 0 }
	);
	const settings = unwrapFrappeMessage(settingsResponse) || initialSettings;
	const creditBalance = unwrapFrappeMessage(creditBalanceResponse);
	const recentJobs = unwrapFrappeMessage(recentJobsResponse) || [];

	const { upload: uploadFile } = useFrappeFileUpload();

	const { call: createJob } = useFrappePostCall(
		"audio_stem.api.separation.create_job_from_file"
	);
	const { call: startSeparation } = useFrappePostCall(
		"audio_stem.api.separation.start_separation"
	);
	const { call: retryFailedJob } = useFrappePostCall(
		"audio_stem.api.separation.retry_failed_job"
	);
	const { call: createJobZip } = useFrappePostCall("audio_stem.api.separation.create_job_zip");
	const { call: getJobDetail } = useFrappePostCall(
		"audio_stem.api.separation.get_job_detail"
	);
	const { call: cancelJob } = useFrappePostCall("audio_stem.api.separation.cancel_job");
	const { call: startTranscription } = useFrappePostCall(
		"audio_stem.api.separation.start_transcription"
	);
	const { call: startKaraokeRender } = useFrappePostCall(
		"audio_stem.api.separation.start_karaoke_render"
	);
	const { call: downloadTranscriptAsset } = useFrappePostCall(
		"audio_stem.api.separation.download_transcript_asset"
	);
	const { call: getTranscriptForEdit } = useFrappePostCall(
		"audio_stem.api.separation.get_transcript_for_edit"
	);
	const { call: saveTranscriptCorrections } = useFrappePostCall(
		"audio_stem.api.separation.save_transcript_corrections"
	);
	const { call: approveTranscriptCorrections } = useFrappePostCall(
		"audio_stem.api.separation.approve_transcript_corrections"
	);
	const { call: resetManualTranscript } = useFrappePostCall(
		"audio_stem.api.separation.reset_manual_transcript"
	);
	const { call: regenerateSubtitleAssets } = useFrappePostCall(
		"audio_stem.api.separation.regenerate_subtitle_assets"
	);
	const { call: downloadManualTranscriptAsset } = useFrappePostCall(
		"audio_stem.api.separation.download_manual_transcript_asset"
	);
	const { call: clearKaraokeBackgroundVideo } = useFrappePostCall(
		"audio_stem.api.separation.clear_karaoke_background_video"
	);
	const { call: updateKaraokeStyleSettings } = useFrappePostCall(
		"audio_stem.api.separation.update_karaoke_style_settings"
	);
	const { call: getKaraokeStyleForJob } = useFrappePostCall(
		"audio_stem.api.separation.get_karaoke_style_for_job"
	);
	const { call: updateKaraokeStyleForJob } = useFrappePostCall(
		"audio_stem.api.separation.update_karaoke_style_for_job"
	);
	const { call: resetKaraokeStyleForJob } = useFrappePostCall(
		"audio_stem.api.separation.reset_karaoke_style_for_job"
	);
	const { call: startLlmSuggestion } = useFrappePostCall(
		"audio_stem.api.separation.start_llm_transcript_suggestion"
	);
	const { call: acceptLlmSuggestion } = useFrappePostCall(
		"audio_stem.api.separation.accept_llm_suggestion_as_manual_draft"
	);
	const { call: suggestScribeKeyterms } = useFrappePostCall(
		"audio_stem.api.separation.suggest_scribe_keyterms"
	);
	const { call: splitLyricsWithLlm } = useFrappePostCall(
		"audio_stem.api.separation.split_lyrics_with_llm"
	);
	const { call: explainQualityWithLlm } = useFrappePostCall(
		"audio_stem.api.separation.explain_transcription_quality_with_llm"
	);

	const displayCurrency = job?.display_currency || settings?.display_currency || "MYR";
	const costPerSecond = settings?.cost_per_second_usd || 0;
	const credit = settings?.credit_management_enabled
		? creditBalance || { enabled: true }
		: { enabled: false };
	const estimatedCost = getEstimatedCost(job, costPerSecond);
	const startDisabled = isStartDisabled({
		job,
		jobName,
		starting,
		enabled: settings?.enabled,
		credit,
		costPerSecond,
		settings,
	});
	const startBlockedReason = getStartBlockedReason({ job, credit, costPerSecond, settings });
	const statusMessage = getJobStatusMessage(job, { starting, retrying, zipping });

	const stopPolling = useCallback(() => {
		if (pollRef.current) {
			clearInterval(pollRef.current);
			pollRef.current = null;
		}
	}, []);

	const fetchJobKaraokeStyle = useCallback(
		async (name = jobName) => {
			if (!name) {
				setJobKaraokeStyle(null);
				return;
			}
			try {
				const style = unwrapFrappeMessage(await getKaraokeStyleForJob({ job_name: name }));
				setJobKaraokeStyle(style);
			} catch (err) {
				setError(parseFrappeError(err) || err.message || "Failed to load karaoke style");
			}
		},
		[getKaraokeStyleForJob, jobName]
	);

	const fetchJobDetail = useCallback(async () => {
		if (!jobName) return;
		const nextJob = unwrapFrappeMessage(await getJobDetail({ job_name: jobName }));
		setJob(nextJob);
		try {
			const style = unwrapFrappeMessage(await getKaraokeStyleForJob({ job_name: jobName }));
			setJobKaraokeStyle(style);
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to load karaoke style");
		}
		const shouldPoll =
			ACTIVE_STATUSES.includes(nextJob?.status) ||
			nextJob?.is_transcription_active ||
			nextJob?.is_karaoke_active ||
			nextJob?.is_llm_suggestion_active;
		if (!shouldPoll) stopPolling();
		if (
			TERMINAL_STATUSES.includes(nextJob?.status) &&
			!nextJob?.is_transcription_active &&
			!nextJob?.is_karaoke_active &&
			!nextJob?.is_llm_suggestion_active
		) {
			refreshRecent();
			refreshCredit();
			refreshSettings();
		}
	}, [getJobDetail, getKaraokeStyleForJob, jobName, refreshCredit, refreshRecent, refreshSettings, stopPolling]);

	const startPolling = useCallback(() => {
		stopPolling();
		fetchJobDetail();
		pollRef.current = setInterval(fetchJobDetail, 3000);
	}, [fetchJobDetail, stopPolling]);

	useEffect(() => {
		if (
			job &&
			(ACTIVE_STATUSES.includes(job.status) ||
				job.is_transcription_active ||
				job.is_karaoke_active ||
				job.is_llm_suggestion_active)
		) {
			startPolling();
		} else {
			stopPolling();
		}
		return () => stopPolling();
	}, [
		job?.status,
		job?.is_transcription_active,
		job?.is_karaoke_active,
		job?.is_llm_suggestion_active,
		jobName,
		startPolling,
		stopPolling,
	]);

	const handleFileChange = async (event) => {
		const file = event.target.files?.[0];
		event.target.value = "";
		if (!file) return;
		setError(null);
		setUploading(true);
		try {
			const uploaded = await uploadFile(
				file,
				{ isPrivate: true },
				undefined,
				"audio_stem.api.separation.upload_audio_file"
			);
			const created = unwrapFrappeMessage(await createJob({ file_url: uploaded.file_url }));
			if (!created?.name) throw new Error("Job was created but no job ID was returned.");
			setJobName(created.name);
			setJob(created);
			await refreshRecent();
			await refreshCredit();
			await refreshSettings();
		} catch (err) {
			setError(getUploadErrorMessage(err, settings));
		} finally {
			setUploading(false);
		}
	};

	const handleStart = async () => {
		if (!jobName || starting || job?.is_active) return;
		if (settings?.enabled === 0) {
			setError("Audio separation is disabled in Audio Separation Settings.");
			return;
		}
		setStarting(true);
		setError(null);
		try {
			const result = unwrapFrappeMessage(await startSeparation({ job_name: jobName }));
			if (result) setJob((prev) => ({ ...prev, ...result }));
			await fetchJobDetail();
			await refreshCredit();
			await refreshSettings();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to start separation");
			await refreshCredit();
		} finally {
			setStarting(false);
		}
	};

	const handleRetry = async (name) => {
		if (retrying) return;
		setRetrying(true);
		setError(null);
		try {
			const result = unwrapFrappeMessage(await retryFailedJob({ job_name: name }));
			setJobName(name);
			if (result) setJob((prev) => ({ ...(prev?.name === name ? prev : {}), ...result }));
			await fetchJobDetail();
			await refreshRecent();
			await refreshCredit();
			await refreshSettings();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to retry job");
		} finally {
			setRetrying(false);
		}
	};

	const handleCancel = async (name) => {
		if (cancelling) return;
		setCancelling(true);
		setError(null);
		try {
			const result = unwrapFrappeMessage(await cancelJob({ job_name: name }));
			setJobName(name);
			setJob(result);
			await refreshRecent();
			await refreshCredit();
			await refreshSettings();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to cancel job");
		} finally {
			setCancelling(false);
		}
	};

	const handleTranscription = async ({
		jobName,
		source,
		language,
		prompt,
		provider,
		scribeModel,
		keyterms,
		noVerbatim,
		tagAudioEvents,
		diarize,
	}) => {
		if (transcribing) return;
		setTranscribing(true);
		setError(null);
		try {
			const result = unwrapFrappeMessage(
				await startTranscription({
					job_name: jobName,
					source,
					language: language || undefined,
					prompt: prompt || undefined,
					provider: provider || undefined,
					scribe_model: scribeModel || undefined,
					keyterms: keyterms || undefined,
					no_verbatim: noVerbatim,
					tag_audio_events: tagAudioEvents,
					diarize: diarize,
				})
			);
			setJobName(jobName);
			setJob(result);
			await fetchJobDetail();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to start transcription");
		} finally {
			setTranscribing(false);
		}
	};

	const handleKaraoke = async (
		name,
		{ karaokeSourceMode = "Auto", karaokeAudioMode = "Auto" } = {}
	) => {
		if (karaokeRendering) return;
		setKaraokeRendering(true);
		setError(null);
		try {
			const result = unwrapFrappeMessage(
				await startKaraokeRender({
					job_name: name,
					karaoke_source_mode: karaokeSourceMode,
					karaoke_audio_mode: karaokeAudioMode,
				})
			);
			setJobName(name);
			setJob(result);
			await fetchJobDetail();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to start karaoke subtitle generation");
		} finally {
			setKaraokeRendering(false);
		}
	};

	const handleClearKaraokeBackground = async (name) => {
		setError(null);
		try {
			await clearKaraokeBackgroundVideo({ job_name: name });
			await fetchJobDetail();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to clear karaoke background video");
		}
	};

	const handleSaveSiteKaraokeStyle = async (stylePayload) => {
		setSavingSiteKaraokeStyle(true);
		setError(null);
		try {
			await updateKaraokeStyleSettings(stylePayload);
			await refreshSettings();
			await fetchJobKaraokeStyle();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to save site karaoke style");
		} finally {
			setSavingSiteKaraokeStyle(false);
		}
	};

	const handleSaveJobKaraokeStyle = async (stylePayload) => {
		if (!jobName) return;
		setSavingJobKaraokeStyle(true);
		setError(null);
		try {
			const style = unwrapFrappeMessage(
				await updateKaraokeStyleForJob({ job_name: jobName, ...stylePayload })
			);
			setJobKaraokeStyle(style);
			await fetchJobDetail();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to save job karaoke style");
		} finally {
			setSavingJobKaraokeStyle(false);
		}
	};

	const handleResetJobKaraokeStyle = async () => {
		if (!jobName) return;
		setSavingJobKaraokeStyle(true);
		setError(null);
		try {
			const style = unwrapFrappeMessage(await resetKaraokeStyleForJob({ job_name: jobName }));
			setJobKaraokeStyle(style);
			await fetchJobDetail();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to reset job karaoke style");
		} finally {
			setSavingJobKaraokeStyle(false);
		}
	};

	const handleUploadKaraokeBackground = async (name, file) => {
		if (!file) return;
		setError(null);
		try {
			await uploadFile(
				file,
				{
					isPrivate: true,
					doctype: "Audio Separation Job",
					docname: name,
					fieldname: "karaoke_background_video_file",
					otherData: { job_name: name },
				},
				"audio_stem.api.separation.upload_karaoke_background_video"
			);
			await fetchJobDetail();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to upload karaoke background video");
		}
	};

	const handleLoadTranscript = useCallback(
		async (name) => unwrapFrappeMessage(await getTranscriptForEdit({ job_name: name })),
		[getTranscriptForEdit]
	);
	const handleSaveTranscript = useCallback(
		async (name, payload) =>
			unwrapFrappeMessage(
				await saveTranscriptCorrections({ job_name: name, payload: JSON.stringify(payload) })
			),
		[saveTranscriptCorrections]
	);
	const handleApproveTranscript = useCallback(
		async (name, payload) => {
			await handleSaveTranscript(name, payload);
			return unwrapFrappeMessage(await approveTranscriptCorrections({ job_name: name }));
		},
		[approveTranscriptCorrections, handleSaveTranscript]
	);
	const handleResetTranscript = useCallback(
		async (name) => {
			const result = unwrapFrappeMessage(await resetManualTranscript({ job_name: name }));
			setJob((prev) => (prev?.name === name ? { ...prev, ...result } : prev));
			return result;
		},
		[resetManualTranscript]
	);
	const handleRegenerateSubtitles = useCallback(
		async (name) => {
			const result = unwrapFrappeMessage(
				await regenerateSubtitleAssets({ job_name: name, source: "manual" })
			);
			setJob((prev) => (prev?.name === name ? { ...prev, ...result } : prev));
			return result;
		},
		[regenerateSubtitleAssets]
	);
	const handleDownloadManualTranscript = useCallback(
		async (name, assetType) => {
			setError(null);
			try {
				const result = unwrapFrappeMessage(
					await downloadManualTranscriptAsset({ job_name: name, asset_type: assetType })
				);
				if (result?.file_url) window.open(result.file_url, "_blank", "noopener,noreferrer");
			} catch (err) {
				setError(parseFrappeError(err) || err.message || "Failed to download manual transcript");
			}
		},
		[downloadManualTranscriptAsset]
	);
	const handleStartLlmSuggestion = useCallback(
		async (name, options = {}) => {
			const result = unwrapFrappeMessage(
				await startLlmSuggestion({
					job_name: name,
					task: options.task || "repair_transcript",
					lyrics_text: options.lyricsText,
					language_hint: options.languageHint,
				})
			);
			setJob((prev) => (prev?.name === name ? { ...prev, ...result } : prev));
			return result;
		},
		[startLlmSuggestion]
	);
	const handleAcceptLlmSuggestion = useCallback(
		async (name) => {
			const result = unwrapFrappeMessage(await acceptLlmSuggestion({ job_name: name }));
			setJob((prev) => (prev?.name === name ? { ...prev, ...result } : prev));
			return result;
		},
		[acceptLlmSuggestion]
	);
	const handleSuggestKeyterms = useCallback(
		async (name, lyricsText, languageHint) => {
			return unwrapFrappeMessage(
				await suggestScribeKeyterms({
					job_name: name,
					lyrics_text: lyricsText,
					language_hint: languageHint,
				})
			);
		},
		[suggestScribeKeyterms]
	);
	const handleSplitLyricsWithLlm = useCallback(
		async (name, lyricsText, languageHint) => {
			const result = unwrapFrappeMessage(
				await splitLyricsWithLlm({
					job_name: name,
					lyrics_text: lyricsText,
					language_hint: languageHint,
				})
			);
			setJob((prev) => (prev?.name === name ? { ...prev, ...result } : prev));
			return result;
		},
		[splitLyricsWithLlm]
	);
	const handleExplainQualityWithLlm = useCallback(
		async (name) => {
			const result = unwrapFrappeMessage(await explainQualityWithLlm({ job_name: name }));
			setJob((prev) => (prev?.name === name ? { ...prev, ...result } : prev));
			return result;
		},
		[explainQualityWithLlm]
	);
	const handleDownloadTranscript = async (name, assetType) => {
		setError(null);
		try {
			const result = unwrapFrappeMessage(
				await downloadTranscriptAsset({ job_name: name, asset_type: assetType })
			);
			if (result?.file_url) window.open(result.file_url, "_blank", "noopener,noreferrer");
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to download transcript");
		}
	};
	const handleZip = async (name) => {
		if (zipping) return;
		setZipping(true);
		setError(null);
		try {
			const result = unwrapFrappeMessage(await createJobZip({ job_name: name }));
			if (result?.zip_file) window.open(result.zip_file, "_blank", "noopener,noreferrer");
			if (jobName === name) await fetchJobDetail();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to create ZIP file");
		} finally {
			setZipping(false);
		}
	};
	const loadJob = async (name) => {
		setJobName(name);
		setError(null);
		const nextJob = unwrapFrappeMessage(await getJobDetail({ job_name: name }));
		setJob(nextJob);
	};

	return (
		<div className="min-h-screen bg-gray-50">
			<header className="border-b border-gray-200 bg-white">
				<div className="mx-auto flex max-w-6xl flex-col gap-2 px-4 py-5 sm:flex-row sm:items-center sm:justify-between">
					<div>
						<h1 className="text-xl font-bold text-gray-900">Audio Vocal Remover</h1>
						<p className="mt-0.5 text-sm text-gray-500">
							Separate vocals, transcribe lyrics, and create karaoke subtitles.
						</p>
					</div>
					<div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:gap-4">
						<StatusPill settings={settings} credit={credit} />
						<div className="text-sm text-gray-600">
							<span>{currentUser}</span>
							<a
								href="/login?redirect-to=/audio-vocal-remover"
								className="ml-3 text-blue-600 hover:underline"
							>
								Switch account
							</a>
						</div>
					</div>
				</div>
			</header>

			<main className="mx-auto max-w-6xl space-y-6 p-4">
				{error && (
					<div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
						{error}
					</div>
				)}

				<div className="grid gap-6 lg:grid-cols-3">
					<div className="space-y-4 lg:col-span-2">
						<UploadCard
							settings={settings}
							uploading={uploading}
							onFileChange={handleFileChange}
							displayCurrency={displayCurrency}
							credit={credit}
							estimatedCost={estimatedCost}
							job={job}
						/>

						<JobDetailPanel
							job={job}
							settings={settings}
							displayCurrency={displayCurrency}
							statusMessage={statusMessage}
							onRetry={handleRetry}
							onZip={handleZip}
							onCancel={handleCancel}
							onTranscription={handleTranscription}
							onKaraoke={handleKaraoke}
							onUploadKaraokeBackground={handleUploadKaraokeBackground}
							onClearKaraokeBackground={handleClearKaraokeBackground}
							onDownloadTranscript={handleDownloadTranscript}
							onLoadTranscript={handleLoadTranscript}
							onSaveTranscript={handleSaveTranscript}
							onApproveTranscript={handleApproveTranscript}
							onResetTranscript={handleResetTranscript}
							onRegenerateSubtitles={handleRegenerateSubtitles}
							onDownloadManualTranscript={handleDownloadManualTranscript}
							onStartLlmSuggestion={handleStartLlmSuggestion}
							onAcceptLlmSuggestion={handleAcceptLlmSuggestion}
							onSuggestKeyterms={handleSuggestKeyterms}
							onSplitLyricsWithLlm={handleSplitLyricsWithLlm}
							onExplainQualityWithLlm={handleExplainQualityWithLlm}
							onJobUpdated={fetchJobDetail}
							onStart={handleStart}
							jobKaraokeStyle={jobKaraokeStyle}
							onSaveJobKaraokeStyle={handleSaveJobKaraokeStyle}
							onResetJobKaraokeStyle={handleResetJobKaraokeStyle}
							onSaveSiteKaraokeStyle={handleSaveSiteKaraokeStyle}
							savingJobKaraokeStyle={savingJobKaraokeStyle}
							savingSiteKaraokeStyle={savingSiteKaraokeStyle}
							starting={starting}
							retrying={retrying}
							zipping={zipping}
							cancelling={cancelling}
							transcribing={transcribing}
							karaokeRendering={karaokeRendering}
							estimatedCost={estimatedCost}
							startDisabled={startDisabled}
							startBlockedReason={startBlockedReason}
						/>
					</div>

					<aside className="space-y-4">
						<JobSidebarSummary
							job={job}
							settings={settings}
							displayCurrency={displayCurrency}
							estimatedCost={estimatedCost}
							credit={credit}
						/>
						<DailyUsageCard
							dailyUsage={settings?.daily_usage}
							displayCurrency={displayCurrency}
						/>
					</aside>
				</div>

				<section className="space-y-3">
					<h2 className="text-base font-semibold text-gray-900">Recent completed jobs</h2>
					<RecentJobsTable
						recentJobs={recentJobs}
						jobName={jobName}
						onOpen={loadJob}
						onCancel={handleCancel}
						onRetry={handleRetry}
						onZip={handleZip}
						cancelling={cancelling}
						retrying={retrying}
						zipping={zipping}
						settings={settings}
					/>
				</section>

				{settings?.is_system_manager && <AdminToolsPanel />}
			</main>
		</div>
	);
}

function AuthenticatedApp({ currentUser }) {
	const { data: settingsResponse, isLoading: settingsLoading } = useFrappeGetCall(
		"audio_stem.api.separation.get_page_settings"
	);
	const settings = unwrapFrappeMessage(settingsResponse);

	if (settingsLoading && !settings) {
		return <div className="p-8 text-center text-gray-500">Loading…</div>;
	}

	if (settings?.pilot_mode_enabled && settings?.pilot_access_allowed === false) {
		return <PilotBlockedView currentUser={currentUser} settings={settings} />;
	}

	return <AudioStemWorkspace currentUser={currentUser} settings={settings} />;
}

function AppContent() {
	const { currentUser, isLoading: authLoading } = useFrappeAuth();
	if (authLoading) return <div className="p-8 text-center text-gray-500">Loading…</div>;
	if (!currentUser || currentUser === "Guest") {
		return (
			<div className="flex min-h-screen items-center justify-center p-6">
				<LoginPrompt />
			</div>
		);
	}
	return <AuthenticatedApp currentUser={currentUser} />;
}

export default function App() {
	return (
		<FrappeProvider>
			<AppContent />
		</FrappeProvider>
	);
}
