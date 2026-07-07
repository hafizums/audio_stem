function ChecklistItem({ item }) {
	const statusClasses = {
		ok: "border-green-200 bg-green-50 text-green-800",
		warning: "border-amber-200 bg-amber-50 text-amber-800",
		error: "border-red-200 bg-red-50 text-red-800",
	};

	return (
		<div
			className={`rounded-md border px-3 py-2 text-sm ${statusClasses[item.status] || statusClasses.warning}`}
		>
			<p className="font-medium">{item.label}</p>
			<p className="mt-1 text-xs opacity-90">{item.message}</p>
		</div>
	);
}

export default function AdminChecklist({ items, loading, error }) {
	if (loading) {
		return <p className="text-sm text-gray-500">Loading configuration checklist...</p>;
	}

	if (error) {
		return <p className="text-sm text-red-600">{error}</p>;
	}

	if (!items?.length) {
		return <p className="text-sm text-gray-500">No checklist items available.</p>;
	}

	return (
		<div className="grid gap-2 sm:grid-cols-2">
			{items.map((item) => (
				<ChecklistItem key={item.key} item={item} />
			))}
		</div>
	);
}
