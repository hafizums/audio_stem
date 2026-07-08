import { memo, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { parseFrappeError } from "./utils";

function cloneTranscript(transcript) {
	return JSON.parse(JSON.stringify(transcript || {}));
}

function shiftTimings(transcript, shiftSeconds) {
	const shift = Number(shiftSeconds) || 0;
	const adjust = (value) => Math.max(0, Number(value || 0) + shift);
	const next = cloneTranscript(transcript);
	next.segments = (next.segments || []).map((segment) => ({
		...segment,
		start: adjust(segment.start),
		end: adjust(segment.end),
		words: (segment.words || []).map((word) => ({
			...word,
			start: adjust(word.start),
			end: adjust(word.end),
		})),
	}));
	next.words = (next.words || []).map((word) => ({
		...word,
		start: adjust(word.start),
		end: adjust(word.end),
	}));
	return next;
}

function snapOverlaps(transcript, minGap = 0) {
	const next = cloneTranscript(transcript);
	const words = [];

	for (const segment of next.segments || []) {
		for (const word of segment.words || []) {
			words.push({ ...word, segmentRef: segment });
		}
	}
	for (const word of next.words || []) {
		words.push({ ...word });
	}

	words.sort((a, b) => Number(a.start) - Number(b.start) || Number(a.end) - Number(b.end));
	let previousEnd = null;
	for (const word of words) {
		if (previousEnd != null && Number(word.start) < previousEnd) {
			word.start = previousEnd + minGap;
			if (Number(word.end) <= Number(word.start)) {
				word.end = Number(word.start) + 0.08;
			}
		}
		previousEnd = Number(word.end);
	}

	for (const segment of next.segments || []) {
		if (!segment.words?.length) continue;
		segment.start = Math.min(...segment.words.map((word) => Number(word.start)));
		segment.end = Math.max(...segment.words.map((word) => Number(word.end)));
		segment.text = segment.words.map((word) => word.text).join(" ").trim();
	}

	return next;
}

function rebuildSegmentText(segment) {
	const words = segment.words || [];
	if (!words.length) return segment;
	return {
		...segment,
		text: words.map((word) => word.text).join(" ").trim(),
	};
}

function validateLocal(transcript, settings) {
	const errors = [];
	const minWord = Number(settings?.subtitle_min_word_duration_seconds || 0.08);

	(transcript?.segments || []).forEach((segment, index) => {
		const start = Number(segment.start);
		const end = Number(segment.end);
		if (start < 0 || end < 0) errors.push(`Segment ${index + 1}: timestamps must be non-negative.`);
		if (end <= start) errors.push(`Segment ${index + 1}: end must be after start.`);
		(segment.words || []).forEach((word, wordIndex) => {
			const wStart = Number(word.start);
			const wEnd = Number(word.end);
			if (wEnd - wStart < minWord) {
				errors.push(`Segment ${index + 1}, word ${wordIndex + 1}: duration is too short.`);
			}
		});
	});

	return errors;
}

function joinSegmentText(segments) {
	return (segments || []).map((segment) => segment.text).join(" ").trim();
}

const SegmentRow = memo(function SegmentRow({
	segment,
	index,
	disabled,
	saving,
	expanded,
	onToggleWords,
	onUpdateSegment,
	onUpdateWord,
}) {
	return (
		<tr>
			<td className="px-2 py-2 align-top">
				<input
					type="number"
					step="0.01"
					className="w-24 rounded border border-gray-300 px-1 py-1"
					value={segment.start ?? 0}
					onChange={(event) => onUpdateSegment(index, "start", Number(event.target.value))}
					disabled={disabled || saving}
				/>
			</td>
			<td className="px-2 py-2 align-top">
				<input
					type="number"
					step="0.01"
					className="w-24 rounded border border-gray-300 px-1 py-1"
					value={segment.end ?? 0}
					onChange={(event) => onUpdateSegment(index, "end", Number(event.target.value))}
					disabled={disabled || saving}
				/>
			</td>
			<td className="px-2 py-2 align-top">
				<input
					type="text"
					className="w-full min-w-40 rounded border border-gray-300 px-1 py-1"
					value={segment.text || ""}
					onChange={(event) => onUpdateSegment(index, "text", event.target.value)}
					disabled={disabled || saving}
				/>
			</td>
			<td className="px-2 py-2 align-top">
				<button
					type="button"
					className="text-xs text-blue-600 hover:underline"
					onClick={() => onToggleWords(index)}
				>
					{expanded ? "Hide" : "Edit"} words
				</button>
				{expanded && (
					<div className="mt-2 space-y-2">
						{(segment.words || []).map((word, wordIndex) => (
							<div
								key={`word-${index}-${wordIndex}`}
								className="grid gap-1 rounded border border-gray-100 p-2 sm:grid-cols-4"
							>
								<input
									type="text"
									className="rounded border border-gray-300 px-1 py-1 text-xs"
									value={word.text || ""}
									onChange={(event) =>
										onUpdateWord(index, wordIndex, "text", event.target.value)
									}
									disabled={disabled || saving}
								/>
								<input
									type="number"
									step="0.01"
									className="rounded border border-gray-300 px-1 py-1 text-xs"
									value={word.start ?? 0}
									onChange={(event) =>
										onUpdateWord(index, wordIndex, "start", Number(event.target.value))
									}
									disabled={disabled || saving}
								/>
								<input
									type="number"
									step="0.01"
									className="rounded border border-gray-300 px-1 py-1 text-xs"
									value={word.end ?? 0}
									onChange={(event) =>
										onUpdateWord(index, wordIndex, "end", Number(event.target.value))
									}
									disabled={disabled || saving}
								/>
							</div>
						))}
					</div>
				)}
			</td>
		</tr>
	);
});

export default function TranscriptEditor({
	job,
	settings,
	disabled,
	onLoad,
	onSave,
	onApprove,
	onReset,
	onRegenerate,
	onDownloadManual,
	onJobUpdated,
}) {
	const [loading, setLoading] = useState(false);
	const [saving, setSaving] = useState(false);
	const [editorState, setEditorState] = useState(null);
	const [transcript, setTranscript] = useState(null);
	const [shiftSeconds, setShiftSeconds] = useState("0");
	const [expandedSegment, setExpandedSegment] = useState(null);
	const [actionError, setActionError] = useState(null);

	const loadedJobRef = useRef(null);
	const onLoadRef = useRef(onLoad);
	onLoadRef.current = onLoad;

	const deferredTranscript = useDeferredValue(transcript);
	const localErrors = useMemo(
		() => (deferredTranscript ? validateLocal(deferredTranscript, settings) : []),
		[deferredTranscript, settings]
	);

	const manualStatus =
		job?.manual_transcript_status || editorState?.manual_transcript_status || "Not Started";

	const fetchTranscript = useCallback(async (jobName, { force = false } = {}) => {
		if (!jobName) return;
		if (!force && loadedJobRef.current === jobName) return;

		setLoading(true);
		setActionError(null);
		try {
			const result = await onLoadRef.current(jobName);
			loadedJobRef.current = jobName;
			setEditorState(result);
			setTranscript(cloneTranscript(result?.transcript));
		} catch (err) {
			setActionError(parseFrappeError(err) || err.message || "Failed to load transcript editor");
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		if (!job?.can_edit_transcript || job?.transcription_status !== "Completed") {
			if (!job?.can_edit_transcript) {
				loadedJobRef.current = null;
				setEditorState(null);
				setTranscript(null);
			}
			return;
		}

		if (loadedJobRef.current === job.name) {
			return;
		}

		let cancelled = false;
		(async () => {
			setLoading(true);
			setActionError(null);
			try {
				const result = await onLoadRef.current(job.name);
				if (cancelled) return;
				loadedJobRef.current = job.name;
				setEditorState(result);
				setTranscript(cloneTranscript(result?.transcript));
			} catch (err) {
				if (!cancelled) {
					setActionError(parseFrappeError(err) || err.message || "Failed to load transcript editor");
				}
			} finally {
				if (!cancelled) setLoading(false);
			}
		})();

		return () => {
			cancelled = true;
		};
	}, [job?.name, job?.can_edit_transcript, job?.transcription_status]);

	const updateSegment = useCallback((index, field, value) => {
		setTranscript((current) => {
			if (!current?.segments?.[index]) return current;
			const segments = current.segments.slice();
			const segment = { ...segments[index] };

			if (field === "text") {
				const text = String(value || "");
				const tokens = text.trim().split(/\s+/).filter(Boolean);
				const existingWords = segment.words || [];
				let words = existingWords;

				if (tokens.length && tokens.length === existingWords.length) {
					words = existingWords.map((word, wordIndex) => ({
						...word,
						text: tokens[wordIndex],
					}));
				} else if (tokens.length) {
					const start = Number(segment.start || 0);
					const end = Number(segment.end || start + 0.08 * tokens.length);
					const step = Math.max((end - start) / tokens.length, 0.08);
					words = tokens.map((token, wordIndex) => ({
						text: token,
						start: start + wordIndex * step,
						end: start + (wordIndex + 1) * step,
					}));
				} else {
					words = [];
				}

				segments[index] = { ...segment, text, words };
			} else {
				segments[index] = { ...segment, [field]: value };
			}

			const next = { ...current, segments };
			if (field === "text") {
				next.text = joinSegmentText(segments);
			}
			return next;
		});
	}, []);

	const updateWord = useCallback((segmentIndex, wordIndex, field, value) => {
		setTranscript((current) => {
			if (!current?.segments?.[segmentIndex]) return current;
			const segments = current.segments.slice();
			const segment = { ...segments[segmentIndex] };
			const words = (segment.words || []).slice();
			words[wordIndex] = { ...words[wordIndex], [field]: value };
			segments[segmentIndex] = rebuildSegmentText({ ...segment, words });
			return {
				...current,
				segments,
				text: joinSegmentText(segments),
			};
		});
	}, []);

	const handleToggleWords = useCallback((index) => {
		setExpandedSegment((current) => (current === index ? null : index));
	}, []);

	const handleShift = () => {
		setTranscript((current) => shiftTimings(current, shiftSeconds));
	};

	const handleSnap = () => {
		setTranscript((current) => snapOverlaps(current));
	};

	const payload = useCallback(
		() => ({
			...transcript,
			text: transcript?.segments?.length ? joinSegmentText(transcript.segments) : transcript?.text,
		}),
		[transcript]
	);

	const runAction = async (action, label) => {
		setSaving(true);
		setActionError(null);
		try {
			const result = await action(job.name, payload());
			if (result) {
				setEditorState((prev) => ({ ...prev, ...result }));
				if (result.transcript) {
					setTranscript(cloneTranscript(result.transcript));
				}
			}
			await onJobUpdated?.();
		} catch (err) {
			setActionError(parseFrappeError(err) || err.message || `Failed to ${label}`);
		} finally {
			setSaving(false);
		}
	};

	if (job?.transcription_status !== "Completed") {
		return null;
	}

	const showInitialLoading = loading && !transcript;

	return (
		<div className="space-y-3 border-t border-gray-100 pt-4">
			<div className="flex flex-wrap items-center justify-between gap-2">
				<h3 className="text-sm font-semibold text-gray-900">Transcript Editor</h3>
				<span className="text-xs text-gray-500">Status: {manualStatus}</span>
			</div>

			{!job.can_edit_transcript && (
				<p className="text-sm text-gray-500">
					Transcript editing is unavailable while karaoke rendering is active.
				</p>
			)}

			{job.can_edit_transcript && (
				<div className="space-y-3">
					{showInitialLoading && (
						<p className="text-sm text-gray-500">Loading transcript...</p>
					)}
					{actionError && <p className="text-sm text-red-600">{actionError}</p>}
					{localErrors.length > 0 && (
						<div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
							{localErrors.map((error) => (
								<p key={error}>{error}</p>
							))}
						</div>
					)}

					{transcript && (
						<>
							<label className="block text-sm text-gray-700">
								Full transcript
								<textarea
									className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
									rows={3}
									value={transcript.text || ""}
									onChange={(event) =>
										setTranscript((current) =>
											current ? { ...current, text: event.target.value } : current
										)
									}
									disabled={disabled || saving}
								/>
							</label>

							<div className="flex flex-wrap items-end gap-2">
								<label className="text-sm text-gray-700">
									Shift all timings (seconds)
									<input
										type="number"
										step="0.1"
										className="mt-1 block w-32 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
										value={shiftSeconds}
										onChange={(event) => setShiftSeconds(event.target.value)}
										disabled={disabled || saving}
									/>
								</label>
								<button
									type="button"
									className="rounded-md border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50 disabled:opacity-50"
									onClick={handleShift}
									disabled={disabled || saving}
								>
									Apply Shift
								</button>
								<button
									type="button"
									className="rounded-md border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50 disabled:opacity-50"
									onClick={handleSnap}
									disabled={disabled || saving}
								>
									Snap Overlaps
								</button>
							</div>

							<div className="overflow-x-auto rounded-md border border-gray-200">
								<table className="min-w-full divide-y divide-gray-200 text-sm">
									<thead className="bg-gray-50">
										<tr>
											<th className="px-2 py-2 text-left font-medium text-gray-600">Start</th>
											<th className="px-2 py-2 text-left font-medium text-gray-600">End</th>
											<th className="px-2 py-2 text-left font-medium text-gray-600">Text</th>
											<th className="px-2 py-2 text-left font-medium text-gray-600">Words</th>
										</tr>
									</thead>
									<tbody className="divide-y divide-gray-100 bg-white">
										{(transcript.segments || []).map((segment, index) => (
											<SegmentRow
												key={`segment-${index}`}
												segment={segment}
												index={index}
												disabled={disabled}
												saving={saving}
												expanded={expandedSegment === index}
												onToggleWords={handleToggleWords}
												onUpdateSegment={updateSegment}
												onUpdateWord={updateWord}
											/>
										))}
									</tbody>
								</table>
							</div>

							<div className="flex flex-wrap gap-2">
								<button
									type="button"
									disabled={disabled || saving}
									onClick={() => runAction(onSave, "save corrections")}
									className="rounded-md bg-slate-700 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
								>
									{saving ? "Saving..." : "Save Draft"}
								</button>
								<button
									type="button"
									disabled={disabled || saving || localErrors.length > 0}
									onClick={() => runAction(onApprove, "approve corrections")}
									className="rounded-md bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
								>
									Approve
								</button>
								<button
									type="button"
									disabled={disabled || saving}
									onClick={async () => {
										setSaving(true);
										setActionError(null);
										try {
											loadedJobRef.current = null;
											await onReset(job.name);
											await fetchTranscript(job.name, { force: true });
											await onJobUpdated?.();
										} catch (err) {
											setActionError(
												parseFrappeError(err) || err.message || "Failed to reset manual edits"
											);
										} finally {
											setSaving(false);
										}
									}}
									className="rounded-md border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50 disabled:opacity-50"
								>
									Reset Manual Edits
								</button>
								<button
									type="button"
									disabled={disabled || saving}
									onClick={async () => {
										setSaving(true);
										setActionError(null);
										try {
											await onRegenerate(job.name);
											await onJobUpdated?.();
										} catch (err) {
											setActionError(
												parseFrappeError(err) || err.message || "Failed to regenerate subtitles"
											);
										} finally {
											setSaving(false);
										}
									}}
									className="rounded-md border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50 disabled:opacity-50"
								>
									Regenerate Subtitles
								</button>
							</div>

							{(job.has_manual_transcript ||
								job.has_manual_transcript_srt ||
								job.has_manual_transcript_vtt) && (
								<div className="flex flex-wrap gap-2">
									{job.has_manual_transcript && (
										<button
											type="button"
											className="text-sm text-blue-600 hover:underline"
											onClick={() => onDownloadManual(job.name, "json")}
										>
											Download manual JSON
										</button>
									)}
									{(job.has_manual_transcript_srt || job.manual_transcript_srt_file) && (
										<button
											type="button"
											className="text-sm text-blue-600 hover:underline"
											onClick={() => onDownloadManual(job.name, "srt")}
										>
											Download manual SRT
										</button>
									)}
									{(job.has_manual_transcript_vtt || job.manual_transcript_vtt_file) && (
										<button
											type="button"
											className="text-sm text-blue-600 hover:underline"
											onClick={() => onDownloadManual(job.name, "vtt")}
										>
											Download manual VTT
										</button>
									)}
								</div>
							)}
						</>
					)}
				</div>
			)}
		</div>
	);
}
