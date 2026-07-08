import { memo, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import {
	Card,
	SafeErrorNotice,
	SecondaryButton,
	StatusBadge,
} from "./components/ui";
import { parseFrappeError } from "./utils";

function cloneTranscript(transcript) {
	return JSON.parse(JSON.stringify(transcript || {}));
}

function formatTimestamp(seconds) {
	const value = Math.max(0, Number(seconds) || 0);
	const mins = Math.floor(value / 60);
	const secs = value - mins * 60;
	return `${mins}:${secs.toFixed(2).padStart(5, "0")}`;
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

function mapSegmentErrors(errors) {
	const map = new Map();
	for (const error of errors) {
		const match = error.match(/Segment (\d+)/);
		if (!match) continue;
		const index = Number(match[1]) - 1;
		if (!map.has(index)) map.set(index, []);
		map.get(index).push(error);
	}
	return map;
}

function countWords(transcript) {
	return (transcript?.segments || []).reduce(
		(total, segment) => total + (segment.words?.length || 0),
		0
	);
}

function transcriptDuration(transcript, fallback = 0) {
	const segmentEnds = (transcript?.segments || []).map((segment) => Number(segment.end || 0));
	const maxEnd = segmentEnds.length ? Math.max(...segmentEnds) : 0;
	return maxEnd || Number(fallback || 0);
}

function ManualStatusBadge({ status }) {
	const styles = {
		"Not Started": "bg-gray-100 text-gray-700 ring-gray-200",
		Draft: "bg-amber-100 text-amber-900 ring-amber-200",
		Saved: "bg-blue-100 text-blue-900 ring-blue-200",
		Approved: "bg-green-100 text-green-900 ring-green-200",
	};
	return (
		<span
			className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
				styles[status] || styles["Not Started"]
			}`}
		>
			{status}
		</span>
	);
}

function EditorSkeleton() {
	return (
		<div className="space-y-3 animate-pulse">
			<div className="h-10 rounded-lg bg-gray-100" />
			<div className="h-8 rounded-lg bg-gray-100" />
			{[1, 2, 3].map((item) => (
				<div key={item} className="h-24 rounded-xl bg-gray-100" />
			))}
		</div>
	);
}

function SegmentTimeline({ segments, duration, activeIndex, onSelect }) {
	const total = Math.max(duration, 1);
	return (
		<div className="relative h-9 overflow-hidden rounded-lg border border-gray-200 bg-gray-50">
			{(segments || []).map((segment, index) => {
				const start = Number(segment.start || 0);
				const end = Number(segment.end || start);
				const left = (start / total) * 100;
				const width = Math.max(((end - start) / total) * 100, 0.6);
				const isActive = activeIndex === index;
				return (
					<button
						key={`timeline-${index}`}
						type="button"
						title={`Line ${index + 1}: ${segment.text || ""}`}
						onClick={() => onSelect(index)}
						className={`absolute top-0 h-full border-r border-white/70 transition ${
							isActive
								? "bg-purple-500 ring-2 ring-purple-400 ring-offset-1"
								: "bg-purple-300/80 hover:bg-purple-400/90"
						}`}
						style={{ left: `${left}%`, width: `${width}%` }}
					/>
				);
			})}
		</div>
	);
}

const SegmentCard = memo(function SegmentCard({
	segment,
	index,
	disabled,
	saving,
	expanded,
	advancedMode,
	errors,
	onToggleWords,
	onUpdateSegment,
	onUpdateWord,
	onFocus,
	cardRef,
}) {
	const hasErrors = errors?.length > 0;
	const wordCount = (segment.words || []).length;

	return (
		<article
			ref={cardRef}
			className={`rounded-xl border bg-white p-4 shadow-sm transition ${
				hasErrors
					? "border-amber-300 ring-1 ring-amber-200"
					: "border-gray-200 hover:border-purple-200"
			}`}
		>
			<div className="flex items-start gap-3">
				<div
					className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
						hasErrors ? "bg-amber-100 text-amber-900" : "bg-purple-50 text-purple-700"
					}`}
				>
					{index + 1}
				</div>

				<div className="min-w-0 flex-1 space-y-3">
					<div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500">
						<span className="font-medium text-gray-700">
							{formatTimestamp(segment.start)} – {formatTimestamp(segment.end)}
						</span>
						<span>{wordCount} word{wordCount === 1 ? "" : "s"}</span>
						{advancedMode && (
							<div className="flex flex-wrap items-center gap-2">
								<label className="inline-flex items-center gap-1">
									<span>Start</span>
									<input
										type="number"
										step="0.01"
										className="w-20 rounded-md border border-gray-300 px-2 py-1 text-xs"
										value={segment.start ?? 0}
										onChange={(event) =>
											onUpdateSegment(index, "start", Number(event.target.value))
										}
										disabled={disabled || saving}
									/>
								</label>
								<label className="inline-flex items-center gap-1">
									<span>End</span>
									<input
										type="number"
										step="0.01"
										className="w-20 rounded-md border border-gray-300 px-2 py-1 text-xs"
										value={segment.end ?? 0}
										onChange={(event) =>
											onUpdateSegment(index, "end", Number(event.target.value))
										}
										disabled={disabled || saving}
									/>
								</label>
							</div>
						)}
					</div>

					<textarea
						rows={Math.min(4, Math.max(2, Math.ceil((segment.text || "").length / 48)))}
						className="w-full resize-y rounded-lg border border-gray-300 px-3 py-2 text-sm leading-relaxed text-gray-900 placeholder:text-gray-400 focus:border-purple-400 focus:outline-none focus:ring-2 focus:ring-purple-100 disabled:bg-gray-50"
						value={segment.text || ""}
						placeholder="Lyric line..."
						onChange={(event) => onUpdateSegment(index, "text", event.target.value)}
						onFocus={onFocus}
						disabled={disabled || saving}
					/>

					{hasErrors && (
						<div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
							{errors.map((error) => (
								<p key={error}>{error}</p>
							))}
						</div>
					)}

					{advancedMode && wordCount > 0 && (
						<div className="border-t border-gray-100 pt-3">
							<button
								type="button"
								className="text-xs font-medium text-purple-700 hover:text-purple-900"
								onClick={() => onToggleWords(index)}
							>
								{expanded ? "Hide word timing" : "Edit word timing"}
							</button>
							{expanded && (
								<div className="mt-3 space-y-2">
									<div className="hidden gap-2 px-1 text-[11px] font-medium uppercase tracking-wide text-gray-400 sm:grid sm:grid-cols-[1fr_5rem_5rem]">
										<span>Word</span>
										<span>Start</span>
										<span>End</span>
									</div>
									{(segment.words || []).map((word, wordIndex) => (
										<div
											key={`word-${index}-${wordIndex}`}
											className="grid gap-2 rounded-lg border border-gray-100 bg-gray-50 p-2 sm:grid-cols-[1fr_5rem_5rem]"
										>
											<input
												type="text"
												className="rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm"
												value={word.text || ""}
												onChange={(event) =>
													onUpdateWord(index, wordIndex, "text", event.target.value)
												}
												disabled={disabled || saving}
											/>
											<input
												type="number"
												step="0.01"
												className="rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm"
												value={word.start ?? 0}
												onChange={(event) =>
													onUpdateWord(index, wordIndex, "start", Number(event.target.value))
												}
												disabled={disabled || saving}
											/>
											<input
												type="number"
												step="0.01"
												className="rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm"
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
						</div>
					)}
				</div>
			</div>
		</article>
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
	const [advancedMode, setAdvancedMode] = useState(false);
	const [showFullTranscript, setShowFullTranscript] = useState(false);
	const [searchQuery, setSearchQuery] = useState("");
	const [activeSegment, setActiveSegment] = useState(0);
	const [actionError, setActionError] = useState(null);

	const loadedJobRef = useRef(null);
	const onLoadRef = useRef(onLoad);
	const segmentRefs = useRef({});
	onLoadRef.current = onLoad;

	const deferredTranscript = useDeferredValue(transcript);
	const localErrors = useMemo(
		() => (deferredTranscript ? validateLocal(deferredTranscript, settings) : []),
		[deferredTranscript, settings]
	);
	const segmentErrorMap = useMemo(() => mapSegmentErrors(localErrors), [localErrors]);

	const manualStatus =
		job?.manual_transcript_status || editorState?.manual_transcript_status || "Not Started";

	const stats = useMemo(() => {
		if (!transcript) return null;
		return {
			segments: (transcript.segments || []).length,
			words: countWords(transcript),
			duration: transcriptDuration(transcript, job?.duration_seconds),
		};
	}, [transcript, job?.duration_seconds]);

	const visibleSegmentIndices = useMemo(() => {
		const segments = transcript?.segments || [];
		const query = searchQuery.trim().toLowerCase();
		if (!query) return segments.map((_, index) => index);
		return segments
			.map((segment, index) => (segment.text?.toLowerCase().includes(query) ? index : -1))
			.filter((index) => index >= 0);
	}, [transcript, searchQuery]);

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

	const scrollToSegment = useCallback((index) => {
		setActiveSegment(index);
		const node = segmentRefs.current[index];
		if (node?.scrollIntoView) {
			node.scrollIntoView({ behavior: "smooth", block: "nearest" });
		}
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
	const sourceLabel =
		editorState?.source === "manual" ? "Manual corrections" : "Original transcription";

	return (
		<div className="space-y-4">
			<Card
				title="Edit Lyrics"
				actions={<ManualStatusBadge status={manualStatus} />}
			>
				<p className="text-sm text-gray-600">
					Fix lyric wording line by line. Approve when ready for karaoke — timing tools are
					optional.
				</p>

				{!job.can_edit_transcript && (
					<p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
						Transcript editing is unavailable while karaoke rendering is active.
					</p>
				)}
			</Card>

			{job.can_edit_transcript && (
				<>
					{showInitialLoading && (
						<Card>
							<EditorSkeleton />
						</Card>
					)}

					{actionError && <SafeErrorNotice message={actionError} />}

					{localErrors.length > 0 && (
						<div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
							<p className="font-medium">Fix timing issues before approving</p>
							<ul className="mt-2 list-disc space-y-1 pl-5">
								{localErrors.slice(0, 6).map((error) => (
									<li key={error}>{error}</li>
								))}
							</ul>
							{localErrors.length > 6 && (
								<p className="mt-2 text-xs text-amber-800">
									{localErrors.length - 6} more issue(s) not shown.
								</p>
							)}
						</div>
					)}

					{transcript && (
						<>
							<Card title="Overview">
								<div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
									<div className="rounded-lg bg-gray-50 px-3 py-2">
										<p className="text-xs text-gray-500">Source</p>
										<p className="text-sm font-medium text-gray-900">{sourceLabel}</p>
									</div>
									<div className="rounded-lg bg-gray-50 px-3 py-2">
										<p className="text-xs text-gray-500">Lines</p>
										<p className="text-sm font-medium text-gray-900">{stats?.segments || 0}</p>
									</div>
									<div className="rounded-lg bg-gray-50 px-3 py-2">
										<p className="text-xs text-gray-500">Words</p>
										<p className="text-sm font-medium text-gray-900">{stats?.words || 0}</p>
									</div>
									<div className="rounded-lg bg-gray-50 px-3 py-2">
										<p className="text-xs text-gray-500">Duration</p>
										<p className="text-sm font-medium text-gray-900">
											{formatTimestamp(stats?.duration || 0)}
										</p>
									</div>
								</div>

								<div className="mt-4">
									<div className="mb-2 flex items-center justify-between gap-2">
										<p className="text-xs font-medium uppercase tracking-wide text-gray-500">
											Timeline
										</p>
										<p className="text-xs text-gray-400">Click a block to jump to a line</p>
									</div>
									<SegmentTimeline
										segments={transcript.segments}
										duration={stats?.duration}
										activeIndex={activeSegment}
										onSelect={scrollToSegment}
									/>
								</div>
							</Card>

							<Card title="Tools">
								<div className="flex flex-wrap items-end gap-3">
									<label className="min-w-[12rem] flex-1 text-sm text-gray-700">
										Search lyrics
										<input
											type="search"
											placeholder="Find a line..."
											className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-purple-400 focus:outline-none focus:ring-2 focus:ring-purple-100"
											value={searchQuery}
											onChange={(event) => setSearchQuery(event.target.value)}
											disabled={disabled || saving}
										/>
									</label>
									<label className="text-sm text-gray-700">
										Shift all timings (s)
										<input
											type="number"
											step="0.1"
											className="mt-1 block w-28 rounded-md border border-gray-300 px-3 py-2 text-sm"
											value={shiftSeconds}
											onChange={(event) => setShiftSeconds(event.target.value)}
											disabled={disabled || saving}
										/>
									</label>
									<SecondaryButton disabled={disabled || saving} onClick={handleShift}>
										Apply shift
									</SecondaryButton>
									<SecondaryButton disabled={disabled || saving} onClick={handleSnap}>
										Snap overlaps
									</SecondaryButton>
								</div>

								<div className="mt-4 flex flex-wrap items-center gap-3 border-t border-gray-100 pt-4">
									<button
										type="button"
										className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
											advancedMode
												? "bg-purple-100 text-purple-800"
												: "bg-gray-100 text-gray-700 hover:bg-gray-200"
										}`}
										onClick={() => setAdvancedMode((value) => !value)}
										disabled={disabled || saving}
									>
										{advancedMode ? "Advanced timing on" : "Advanced timing off"}
									</button>
									<button
										type="button"
										className="text-xs font-medium text-purple-700 hover:text-purple-900"
										onClick={() => setShowFullTranscript((value) => !value)}
										disabled={disabled || saving}
									>
										{showFullTranscript ? "Hide full transcript" : "Show full transcript"}
									</button>
									{searchQuery && (
										<span className="text-xs text-gray-500">
											Showing {visibleSegmentIndices.length} of {stats?.segments || 0} lines
										</span>
									)}
								</div>

								{showFullTranscript && (
									<label className="mt-4 block text-sm text-gray-700">
										Full transcript
										<textarea
											className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm leading-relaxed"
											rows={4}
											value={transcript.text || ""}
											onChange={(event) =>
												setTranscript((current) =>
													current ? { ...current, text: event.target.value } : current
												)
											}
											disabled={disabled || saving}
										/>
									</label>
								)}
							</Card>

							<div className="space-y-3">
								<div className="flex items-center justify-between gap-2 px-1">
									<h4 className="text-sm font-semibold text-gray-900">Lyric lines</h4>
									{localErrors.length > 0 && (
										<StatusBadge status={`${localErrors.length} issue(s)`} />
									)}
								</div>

								{visibleSegmentIndices.length === 0 ? (
									<div className="rounded-xl border border-dashed border-gray-300 bg-white px-5 py-8 text-center text-sm text-gray-500">
										No lines match your search.
									</div>
								) : (
									visibleSegmentIndices.map((index) => {
										const segment = transcript.segments[index];
										return (
											<SegmentCard
												key={`segment-${index}`}
												cardRef={(node) => {
													segmentRefs.current[index] = node;
												}}
												segment={segment}
												index={index}
												disabled={disabled}
												saving={saving}
												expanded={expandedSegment === index}
												advancedMode={advancedMode}
												errors={segmentErrorMap.get(index)}
												onToggleWords={handleToggleWords}
												onUpdateSegment={updateSegment}
												onUpdateWord={updateWord}
												onFocus={() => setActiveSegment(index)}
											/>
										);
									})
								)}
							</div>

							<Card className="sticky bottom-3 z-10 border-purple-200 bg-white/95 backdrop-blur">
								<div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
									<div className="text-xs text-gray-500">
										<p>Save a draft anytime. Approve locks lyrics for karaoke.</p>
										<p className="mt-1">Download manual JSON/SRT/VTT from the Downloads tab.</p>
									</div>
									<div className="flex flex-wrap gap-2">
										<SecondaryButton
											disabled={disabled || saving}
											onClick={() => runAction(onSave, "save corrections")}
										>
											{saving ? "Saving..." : "Save draft"}
										</SecondaryButton>
										<button
											type="button"
											disabled={disabled || saving || localErrors.length > 0}
											onClick={() => runAction(onApprove, "approve corrections")}
											className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
										>
											Approve for karaoke
										</button>
										<SecondaryButton
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
														parseFrappeError(err) ||
															err.message ||
															"Failed to reset manual edits"
													);
												} finally {
													setSaving(false);
												}
											}}
										>
											Reset
										</SecondaryButton>
										<SecondaryButton
											disabled={disabled || saving}
											onClick={async () => {
												setSaving(true);
												setActionError(null);
												try {
													await onRegenerate(job.name);
													await onJobUpdated?.();
												} catch (err) {
													setActionError(
														parseFrappeError(err) ||
															err.message ||
															"Failed to regenerate subtitles"
													);
												} finally {
													setSaving(false);
												}
											}}
										>
											Regenerate subtitles
										</SecondaryButton>
									</div>
								</div>
							</Card>
						</>
					)}
				</>
			)}
		</div>
	);
}
