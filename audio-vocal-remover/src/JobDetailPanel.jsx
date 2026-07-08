import { useEffect, useState } from "react";
import TranscriptEditor from "./TranscriptEditor";
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
	onCancel,
	onTranscription,
	onKaraoke,
	onDownloadTranscript,
	onLoadTranscript,
	onSaveTranscript,
	onApproveTranscript,
	onResetTranscript,
	onRegenerateSubtitles,
	onDownloadManualTranscript,
	onJobUpdated,
	retrying,
	zipping,
	cancelling,
	transcribing,
	karaokeRendering,
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
	const [transcriptionSource, setTranscriptionSource] = useState("Vocal");
	const [transcriptionLanguage, setTranscriptionLanguage] = useState(
		job.default_transcription_language || settings?.default_transcription_language || ""
	);
	const [karaokeSourceMode, setKaraokeSourceMode] = useState(job.karaoke_source_mode || "Auto");

	useEffect(() => {
		setKaraokeSourceMode(job.karaoke_source_mode || "Auto");
	}, [job.karaoke_source_mode, job.name]);

	const selectedKaraokeSourceLabel =
		karaokeSourceMode === "Original Whisper"
			? "Original Whisper"
			: karaokeSourceMode === "Manual Corrected"
				? "Manual Corrected"
				: job.manual_transcript_is_approved && job.has_manual_transcript
					? "Manual Corrected"
					: "Original Whisper";
	const karaokeNeedsRegenerate =
		job.karaoke_status === "Completed" &&
		job.karaoke_rendered_transcript_source_label &&
		job.karaoke_rendered_transcript_source_label !== selectedKaraokeSourceLabel;

	const vocalTranscriptionBlocked =
		job.is_active ||
		job.status !== "Completed" ||
		!job.has_vocal;
	const transcriptionDisabled =
		!job.openai_enabled ||
		!job.can_start_transcription ||
		transcribing ||
		job.is_transcription_active ||
		(transcriptionSource === "Vocal" && vocalTranscriptionBlocked);
	const karaokeDisabled =
		!job.karaoke_enabled ||
		!job.can_start_karaoke ||
		karaokeRendering ||
		job.is_karaoke_active;
	const karaokeInfoMessage =
		job.karaoke_status === "Completed" &&
		job.karaoke_error &&
		(job.karaoke_error.includes("Video render is disabled") ||
			job.karaoke_error.includes("Video render failed"));

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

			{job.cancellation_requested && job.status !== "Cancelled" && (
				<div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
					Cancellation requested. The current provider job may still finish.
				</div>
			)}

			{job.status === "Cancelled" && (
				<div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700">
					This job was cancelled.
					{job.cancel_reason ? ` Reason: ${job.cancel_reason}` : ""}
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
				{job.can_cancel && (
					<button
						type="button"
						disabled={cancelling}
						onClick={() => onCancel(job.name)}
						className="rounded-md bg-gray-700 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
					>
						{cancelling ? "Cancelling..." : "Cancel Job"}
					</button>
				)}
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

			<div className="space-y-3 border-t border-gray-100 pt-4">
				<h3 className="text-sm font-semibold text-gray-900">Transcription</h3>
				{!job.openai_enabled && !settings?.openai_enabled ? (
					<p className="text-sm text-gray-500">OpenAI transcription is disabled.</p>
				) : (
					<div className="space-y-3">
						<div className="flex flex-wrap items-center gap-2">
							<StatusBadge status={job.transcription_status || "Not Started"} />
							{job.transcription_cost_usd > 0 && (
								<span className="text-xs text-gray-500">
									Cost: {formatCost(job.transcription_cost_usd, displayCurrency)}
								</span>
							)}
						</div>
						<div className="grid gap-2 sm:grid-cols-2">
							<label className="text-sm text-gray-700">
								Source
								<select
									className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
									value={transcriptionSource}
									onChange={(e) => setTranscriptionSource(e.target.value)}
									disabled={transcribing || job.is_transcription_active}
								>
									<option value="Vocal">Vocal</option>
									<option value="Original">Original</option>
								</select>
							</label>
							<label className="text-sm text-gray-700">
								Language (optional)
								<input
									type="text"
									className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
									value={transcriptionLanguage}
									onChange={(e) => setTranscriptionLanguage(e.target.value)}
									placeholder="Auto-detect"
									disabled={transcribing || job.is_transcription_active}
								/>
							</label>
						</div>
						<button
							type="button"
							disabled={transcriptionDisabled}
							onClick={() =>
								onTranscription(job.name, transcriptionSource, transcriptionLanguage)
							}
							className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
						>
							{transcribing || job.is_transcription_active
								? "Transcribing..."
								: "Start Transcription"}
						</button>
						{job.transcription_blocked_reason && transcriptionDisabled && (
							<p className="text-sm text-gray-600">{job.transcription_blocked_reason}</p>
						)}
						{job.transcription_error && (
							<p className="text-sm text-red-600">{job.transcription_error}</p>
						)}
						{job.transcript_text && (
							<div className="rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-800">
								<p className="mb-1 font-medium text-gray-700">Transcript preview</p>
								<p className="max-h-40 overflow-y-auto whitespace-pre-wrap">{job.transcript_text}</p>
							</div>
						)}
						{(job.has_transcript_json || job.has_transcript_srt || job.has_transcript_vtt) && (
							<div className="flex flex-wrap gap-2">
								{job.has_transcript_json && (
									<button
										type="button"
										className="text-sm text-blue-600 hover:underline"
										onClick={() => onDownloadTranscript(job.name, "json")}
									>
										Download JSON
									</button>
								)}
								{job.has_transcript_srt && (
									<button
										type="button"
										className="text-sm text-blue-600 hover:underline"
										onClick={() => onDownloadTranscript(job.name, "srt")}
									>
										Download SRT
									</button>
								)}
								{job.has_transcript_vtt && (
									<button
										type="button"
										className="text-sm text-blue-600 hover:underline"
										onClick={() => onDownloadTranscript(job.name, "vtt")}
									>
										Download VTT
									</button>
								)}
							</div>
						)}
					</div>
				)}
			</div>

			<TranscriptEditor
				job={job}
				settings={settings}
				disabled={!job.can_edit_transcript || job.is_karaoke_active || karaokeRendering}
				onLoad={onLoadTranscript}
				onSave={onSaveTranscript}
				onApprove={onApproveTranscript}
				onReset={onResetTranscript}
				onRegenerate={onRegenerateSubtitles}
				onDownloadManual={onDownloadManualTranscript}
				onJobUpdated={onJobUpdated}
			/>

			<div className="space-y-3 border-t border-gray-100 pt-4">
				<h3 className="text-sm font-semibold text-gray-900">Karaoke Subtitles</h3>
				{!job.karaoke_enabled && !settings?.karaoke_enabled ? (
					<p className="text-sm text-gray-500">Karaoke subtitle generation is disabled.</p>
				) : (
					<div className="space-y-3">
						<div className="flex flex-wrap items-center gap-2">
							<StatusBadge status={job.karaoke_status || "Not Started"} />
							{job.karaoke_engine_version && (
								<span className="text-xs text-gray-500">karaoke_engine {job.karaoke_engine_version}</span>
							)}
						</div>
						{settings?.karaoke_video_render_enabled && (
							<p className="text-xs text-gray-500">MP4 video rendering is enabled on this site.</p>
						)}
						<label className="block text-sm text-gray-700">
							Karaoke source
							<select
								className="mt-1 w-full max-w-xs rounded-md border border-gray-300 px-2 py-1.5 text-sm"
								value={karaokeSourceMode}
								onChange={(event) => setKaraokeSourceMode(event.target.value)}
								disabled={karaokeDisabled}
							>
								<option value="Auto">Auto</option>
								<option value="Original Whisper">Original Whisper</option>
								<option value="Manual Corrected">Manual Corrected</option>
							</select>
						</label>
						{job.karaoke_rendered_transcript_source_label && (
							<p className="text-xs text-gray-500">
								Last render used: {job.karaoke_rendered_transcript_source_label}
							</p>
						)}
						<p className="text-xs text-gray-500">
							Next render will use: {selectedKaraokeSourceLabel}
						</p>
						{karaokeNeedsRegenerate && (
							<p className="text-sm text-amber-700">
								The current karaoke output does not match the selected source. Click regenerate to
								update the ASS/MP4.
							</p>
						)}
						<button
							type="button"
							disabled={karaokeDisabled}
							onClick={() => onKaraoke(job.name, karaokeSourceMode)}
							className="rounded-md bg-purple-600 px-3 py-2 text-sm font-medium text-white hover:bg-purple-700 disabled:cursor-not-allowed disabled:opacity-50"
						>
							{karaokeRendering || job.is_karaoke_active
								? "Generating..."
								: job.karaoke_status === "Completed"
									? "Regenerate Karaoke Subtitle"
									: "Generate Karaoke Subtitle"}
						</button>
						{job.karaoke_blocked_reason && karaokeDisabled && (
							<p className="text-sm text-gray-600">{job.karaoke_blocked_reason}</p>
						)}
						{karaokeInfoMessage && (
							<p className="text-sm text-gray-600">{job.karaoke_error}</p>
						)}
						{job.karaoke_error && !karaokeInfoMessage && (
							<p className="text-sm text-red-600">{job.karaoke_error}</p>
						)}
						{(job.has_karaoke_ass || job.karaoke_ass_file) && (
							<a
								href={job.karaoke_ass_file}
								className="inline-block text-sm text-blue-600 hover:underline"
								download
							>
								Download ASS
							</a>
						)}
						{job.karaoke_video_file && (
							<div>
								<p className="mb-2 text-sm font-medium text-gray-700">Karaoke video preview</p>
								<video
									controls
									preload="none"
									src={job.karaoke_video_file}
									className="w-full max-w-full rounded-md"
								/>
								<a
									href={job.karaoke_video_file}
									className="mt-2 inline-block text-sm text-blue-600 hover:underline"
									download
								>
									Download karaoke MP4
								</a>
							</div>
						)}
					</div>
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
