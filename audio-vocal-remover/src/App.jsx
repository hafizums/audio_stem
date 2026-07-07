import { useCallback, useEffect, useRef, useState } from "react";
import {
	FrappeProvider,
	useFrappeAuth,
	useFrappeFileUpload,
	useFrappeGetCall,
	useFrappePostCall,
} from "frappe-react-sdk";
import {
	ACTIVE_STATUSES,
	TERMINAL_STATUSES,
	formatCost,
	formatDateTime,
	getEstimatedCost,
	getJobStatusMessage,
	getStartBlockedReason,
	getStatusBadgeClass,
	getUploadErrorMessage,
	isStartDisabled,
	parseFrappeError,
	unwrapFrappeMessage,
} from "./utils";

function Section({ title, children }) {
	return (
		<section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
			<h2 className="mb-3 text-base font-semibold text-gray-900">{title}</h2>
			{children}
		</section>
	);
}

function StatusBadge({ status }) {
	return (
		<span
			className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${getStatusBadgeClass(status)}`}
		>
			{status}
		</span>
	);
}

function LoginPrompt() {
	return (
		<div className="mx-auto max-w-lg rounded-lg border border-amber-200 bg-amber-50 p-6 text-center">
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

function AuthenticatedApp({ currentUser }) {
	const [jobName, setJobName] = useState(null);
	const [job, setJob] = useState(null);
	const [starting, setStarting] = useState(false);
	const [retrying, setRetrying] = useState(false);
	const [zipping, setZipping] = useState(false);
	const [uploading, setUploading] = useState(false);
	const [error, setError] = useState(null);
	const pollRef = useRef(null);

	const { data: settingsResponse } = useFrappeGetCall(
		"audio_stem.api.separation.get_page_settings"
	);
	const { data: creditBalanceResponse, mutate: refreshCredit } = useFrappeGetCall(
		"audio_stem.api.separation.get_my_credit_balance"
	);
	const { data: recentJobsResponse, mutate: refreshRecent } = useFrappeGetCall(
		"audio_stem.api.separation.get_recent_jobs",
		{ limit: 10 }
	);
	const settings = unwrapFrappeMessage(settingsResponse);
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
	const { call: createJobZip } = useFrappePostCall(
		"audio_stem.api.separation.create_job_zip"
	);
	const { call: getJobStatus } = useFrappePostCall(
		"audio_stem.api.separation.get_job_status"
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
	});
	const startBlockedReason = getStartBlockedReason({ job, credit, costPerSecond, settings });
	const statusMessage = getJobStatusMessage(job, { starting, retrying, zipping });

	const stopPolling = useCallback(() => {
		if (pollRef.current) {
			clearInterval(pollRef.current);
			pollRef.current = null;
		}
	}, []);

	const fetchJobStatus = useCallback(async () => {
		if (!jobName) return;
		const nextJob = unwrapFrappeMessage(await getJobStatus({ job_name: jobName }));
		setJob(nextJob);
		if (!ACTIVE_STATUSES.includes(nextJob?.status)) {
			stopPolling();
		}
		if (TERMINAL_STATUSES.includes(nextJob?.status)) {
			refreshRecent();
			refreshCredit();
		}
	}, [getJobStatus, jobName, refreshCredit, refreshRecent, stopPolling]);

	const startPolling = useCallback(() => {
		stopPolling();
		fetchJobStatus();
		pollRef.current = setInterval(fetchJobStatus, 3000);
	}, [fetchJobStatus, stopPolling]);

	useEffect(() => {
		if (job && ACTIVE_STATUSES.includes(job.status)) {
			startPolling();
		} else {
			stopPolling();
		}
		return () => stopPolling();
	}, [job?.status, jobName, startPolling, stopPolling]);

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
			const created = unwrapFrappeMessage(
				await createJob({ file_url: uploaded.file_url })
			);
			if (!created?.name) {
				throw new Error("Job was created but no job ID was returned.");
			}
			setJobName(created.name);
			setJob(created);
			await refreshRecent();
			await refreshCredit();
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
			const result = unwrapFrappeMessage(
				await startSeparation({ job_name: jobName })
			);
			if (result) {
				setJob((prev) => ({ ...prev, ...result }));
			}
			await fetchJobStatus();
			await refreshCredit();
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
			if (result) {
				setJob((prev) => ({ ...(prev?.name === name ? prev : {}), ...result }));
			}
			await fetchJobStatus();
			await refreshRecent();
			await refreshCredit();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to retry job");
		} finally {
			setRetrying(false);
		}
	};

	const handleZip = async (name) => {
		if (zipping) return;
		setZipping(true);
		setError(null);
		try {
			const result = unwrapFrappeMessage(await createJobZip({ job_name: name }));
			if (result?.zip_file) {
				window.open(result.zip_file, "_blank", "noopener,noreferrer");
			}
			if (jobName === name) {
				await fetchJobStatus();
			}
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to create ZIP file");
		} finally {
			setZipping(false);
		}
	};

	const loadJob = async (name) => {
		setJobName(name);
		setError(null);
		const nextJob = unwrapFrappeMessage(await getJobStatus({ job_name: name }));
		setJob(nextJob);
	};

	return (
		<div className="min-h-screen bg-gray-100">
			<header className="border-b border-gray-200 bg-white">
				<div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-4">
					<div>
						<h1 className="text-lg font-bold text-gray-900">Audio Vocal Remover</h1>
						<p className="text-sm text-gray-500">Separate vocals and instrumentals with WaveSpeed</p>
					</div>
					<div className="text-right text-sm text-gray-600">
						<p>{currentUser}</p>
						<a href="/login?redirect-to=/audio-vocal-remover" className="text-blue-600 hover:underline">
							Switch account
						</a>
					</div>
				</div>
			</header>

			<main className="mx-auto max-w-5xl space-y-4 p-4">
				{error && (
					<div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
						{error}
					</div>
				)}

				<Section title="Upload Audio">
					{jobName && (
						<p className="mb-3 text-sm text-gray-500">
							Job created. Upload another file to start over.
						</p>
					)}
					<label className="inline-flex cursor-pointer items-center rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800">
						{uploading ? "Uploading..." : "Upload Audio File"}
						<input
							type="file"
							className="hidden"
							accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg,.aac"
							disabled={uploading}
							onChange={handleFileChange}
						/>
					</label>
				</Section>

				<Section title="Limits">
					<p className="text-sm text-gray-600">
						{settings
							? `Max file size: ${settings.max_file_size_mb} MB. Max duration: ${settings.max_audio_duration_seconds} seconds.`
							: "Loading limits..."}
					</p>
				</Section>

				<Section title="Cost Estimate">
					{!job ? (
						<p className="text-sm text-gray-500">Upload an audio file to see the estimate.</p>
					) : job.duration_seconds ? (
						<p className="text-sm text-gray-700">
							Duration: <strong>{job.duration_seconds}s</strong>
							<br />
							Estimated provider cost:{" "}
							<strong>{formatCost(estimatedCost, displayCurrency)}</strong>
						</p>
					) : (
						<p className="text-sm text-amber-700">
							Audio duration is unknown. Separation cannot be started until duration is available.
						</p>
					)}
				</Section>

				<Section title="Credits">
					{!settings?.credit_management_enabled ? (
						<p className="text-sm text-gray-500">Credit management is not enabled.</p>
					) : credit.error ? (
						<p className="text-sm text-red-600">
							Credit integration is unavailable: {credit.error}
						</p>
					) : (
						<div className="space-y-1 text-sm text-gray-700">
							<p>
								Credit type: <strong>{credit.credit_type}</strong>
							</p>
							<p>
								Current balance:{" "}
								<strong>{formatCost(credit.current_balance, displayCurrency)}</strong>
							</p>
							<p>
								Reserved balance:{" "}
								<strong>{formatCost(credit.reserved_balance, displayCurrency)}</strong>
							</p>
							<p>
								Available balance:{" "}
								<strong>{formatCost(credit.available_balance, displayCurrency)}</strong>
							</p>
							<p>
								{job?.duration_seconds ? (
									<>
										Estimated job cost:{" "}
										<strong>{formatCost(estimatedCost, displayCurrency)}</strong>
									</>
								) : (
									"Estimated job cost will appear after upload."
								)}
							</p>
						</div>
					)}
				</Section>

				<Section title="Start Separation">
					<button
						type="button"
						disabled={startDisabled || job?.is_active}
						onClick={handleStart}
						className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
					>
						{starting ? "Starting..." : "Start Separation"}
					</button>
					{startBlockedReason && (
						<p className="mt-2 text-sm text-gray-600">{startBlockedReason}</p>
					)}
				</Section>

				<Section title="Job Status">
					{job ? (
						<div className="space-y-2">
							<p className="font-medium text-gray-900">
								Job <strong>{job.name}</strong>{" "}
								<StatusBadge status={job.status} />
							</p>
							<p className="text-sm text-gray-600">{statusMessage}</p>
							{settings?.credit_management_enabled && job.credit_status && (
								<p className="text-sm text-gray-600">
									Credit status: <strong>{job.credit_status}</strong>
								</p>
							)}
							{settings?.credit_management_enabled && job.credit_error && (
								<p className="text-sm text-red-600">{job.credit_error}</p>
							)}
							{job.status === "Failed" && job.can_retry && (
								<button
									type="button"
									disabled={retrying || job.is_active}
									onClick={() => handleRetry(job.name)}
									className="rounded-md bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-50"
								>
									{retrying ? "Retrying..." : "Retry Job"}
								</button>
							)}
							{job.can_zip && (
								<button
									type="button"
									disabled={zipping}
									onClick={() => handleZip(job.name)}
									className="ml-2 rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50"
								>
									{zipping ? "Creating ZIP..." : "Download ZIP"}
								</button>
							)}
						</div>
					) : (
						<p className="text-sm text-gray-500">No active job</p>
					)}
				</Section>

				<Section title="Original Audio">
					{job?.original_file ? (
						<audio controls preload="none" src={job.original_file} className="w-full" />
					) : (
						<p className="text-sm text-gray-500">Original audio not available.</p>
					)}
				</Section>

				<Section title="Vocal Output">
					{job?.vocal_output_url || job?.vocal_file ? (
						<>
							<audio
								controls
								preload="none"
								src={job.vocal_output_url || job.vocal_file}
								className="w-full"
							/>
							<a
								href={job.vocal_output_url || job.vocal_file}
								className="mt-2 inline-block text-sm text-blue-600 hover:underline"
								download
							>
								Download Vocal
							</a>
						</>
					) : (
						<p className="text-sm text-gray-500">Vocal output not available yet.</p>
					)}
				</Section>

				<Section title="Instrumental Output">
					{job?.instrumental_output_url || job?.instrumental_file ? (
						<>
							<audio
								controls
								preload="none"
								src={job.instrumental_output_url || job.instrumental_file}
								className="w-full"
							/>
							<a
								href={job.instrumental_output_url || job.instrumental_file}
								className="mt-2 inline-block text-sm text-blue-600 hover:underline"
								download
							>
								Download Instrumental
							</a>
						</>
					) : (
						<p className="text-sm text-gray-500">Instrumental output not available yet.</p>
					)}
				</Section>

				<Section title="Recent Jobs">
					{!recentJobs?.length ? (
						<p className="text-sm text-gray-500">No jobs yet.</p>
					) : (
						<div className="overflow-x-auto">
							<table className="min-w-full border border-gray-200 text-sm">
								<thead className="bg-gray-50">
									<tr>
										<th className="border-b px-3 py-2 text-left">Job</th>
										<th className="border-b px-3 py-2 text-left">File</th>
										<th className="border-b px-3 py-2 text-left">Status</th>
										{settings?.credit_management_enabled && (
											<th className="border-b px-3 py-2 text-left">Credit</th>
										)}
										<th className="border-b px-3 py-2 text-left">Duration</th>
										<th className="border-b px-3 py-2 text-left">Cost</th>
										<th className="border-b px-3 py-2 text-left">Created</th>
										<th className="border-b px-3 py-2 text-left">Completed</th>
										<th className="border-b px-3 py-2 text-left">Outputs</th>
										<th className="border-b px-3 py-2 text-left">Actions</th>
									</tr>
								</thead>
								<tbody>
									{recentJobs.map((row) => (
										<tr key={row.name} className="hover:bg-gray-50">
											<td className="border-b px-3 py-2 font-medium text-gray-900">{row.name}</td>
											<td className="border-b px-3 py-2 text-gray-600">
												{row.original_filename || "—"}
											</td>
											<td className="border-b px-3 py-2">
												<StatusBadge status={row.status} />
												{row.error_summary && (
													<p className="mt-1 text-xs text-red-600">{row.error_summary}</p>
												)}
											</td>
											{settings?.credit_management_enabled && (
												<td className="border-b px-3 py-2">{row.credit_status || "—"}</td>
											)}
											<td className="border-b px-3 py-2">
												{row.duration_seconds ? `${row.duration_seconds}s` : "—"}
											</td>
											<td className="border-b px-3 py-2">
												{row.provider_cost_usd
													? formatCost(row.provider_cost_usd, displayCurrency)
													: "—"}
											</td>
											<td className="border-b px-3 py-2 text-gray-600">
												{formatDateTime(row.creation)}
											</td>
											<td className="border-b px-3 py-2 text-gray-600">
												{formatDateTime(row.completed_at)}
											</td>
											<td className="border-b px-3 py-2 text-xs text-gray-600">
												{row.has_vocal ? "Vocal" : "—"}
												{row.has_vocal && row.has_instrumental ? " / " : ""}
												{row.has_instrumental ? "Instrumental" : ""}
											</td>
											<td className="border-b px-3 py-2">
												<div className="flex flex-wrap gap-2">
													<button
														type="button"
														onClick={() => loadJob(row.name)}
														className="text-blue-600 hover:underline"
													>
														Open
													</button>
													{row.can_retry && (
														<button
															type="button"
															disabled={retrying}
															onClick={() => handleRetry(row.name)}
															className="text-amber-700 hover:underline disabled:opacity-50"
														>
															Retry
														</button>
													)}
													{row.can_zip && (
														<button
															type="button"
															disabled={zipping}
															onClick={() => handleZip(row.name)}
															className="text-green-700 hover:underline disabled:opacity-50"
														>
															ZIP
														</button>
													)}
												</div>
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					)}
				</Section>
			</main>
		</div>
	);
}

function AppContent() {
	const { currentUser, isLoading: authLoading } = useFrappeAuth();

	if (authLoading) {
		return <div className="p-8 text-center text-gray-500">Loading...</div>;
	}

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
