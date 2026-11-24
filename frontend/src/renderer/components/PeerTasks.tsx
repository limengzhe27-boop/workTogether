import { PeerTaskEvidence, PeerIntegrationEvidence } from "./PeerEvidence";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../lib/api-client";
import type { components } from "../../api/schema";
import { ConfirmDialog } from "./ConfirmDialog";
import { Button } from "./ui/button";

type Task = components["schemas"]["PeerTask"];
const labels: Record<string, string> = {
	open: "待认领",
	claimed: "进行中",
	submitted: "待独立验收",
	accepted: "已验收（尚未集成）",
};
export function usePeerTasks(roomId: string, enabled = true) {
	const key = ["peer-tasks", roomId];
	return useQuery({
		queryKey: key,
		enabled,
		refetchInterval: 3000,
		retry: false,
		queryFn: async () => {
			const r = await apiClient.GET("/api/v1/peer-rooms/{roomId}/tasks", {
				params: { path: { roomId } },
			});
			if (r.error || !r.data) throw new Error("任务读取失败");
			return r.data;
		},
	});
}

export function PeerTasks({
	roomId,
	epoch = 0,
	paused = false,
	label = (id: string) => id,
}: {
	roomId: string;
	epoch?: number;
	paused?: boolean;
	label?: (id: string) => string;
}) {
	const cache = useQueryClient();
	const [goal, setGoal] = useState("");
	const [base, setBase] = useState("");
	const [name, setName] = useState("");
	const [candidate, setCandidate] = useState("");
	const [integrationError, setIntegrationError] = useState("");
	const [expanded, setExpanded] = useState(false);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");
	const [releasing, setReleasing] = useState<Task | null>(null);
	const [releaseError, setReleaseError] = useState("");
	const query = usePeerTasks(roomId);
	const goalValid = goal.trim() !== "" && new TextEncoder().encode(goal).length <= 4000;
	const readyForIntegration = !!query.data?.length && query.data.every(
		(t) => t.state === "accepted" && t.owner && t.reviewer && t.owner !== t.reviewer && /^[a-f0-9]{40}$/i.test(t.commit),
	);
	const validCandidateName = /^[a-zA-Z0-9][a-zA-Z0-9-]{0,60}$/.test(name);

	async function integrate() {
		setBusy(true);
		setCandidate("");
		setIntegrationError("");
		try {
			const r = await apiClient.POST("/api/v1/peer-rooms/{roomId}/integrate", {
				params: { path: { roomId } },
				body: { epoch, base, name },
			});
			if (!r.data || r.error) throw new Error(r.error?.message || "候选集成未完成");
			setCandidate(`${r.data.branch} · ${r.data.commit}`);
			await cache.invalidateQueries({ queryKey: ["peer-integration-evidence", roomId], exact: true });
		} catch (e) {
			setIntegrationError(e instanceof Error ? e.message : "候选集成未完成");
		} finally {
			setBusy(false);
		}
	}
	async function release() {
		if (!releasing || busy) return;
		setBusy(true);
		setReleaseError("");
		try {
			const response = await apiClient.POST("/api/v1/peer-rooms/{roomId}/tasks/{taskId}/release", {
				params: { path: { roomId, taskId: releasing.id } },
				body: { epoch, revision: releasing.revision },
			});
			if (response.error || !response.data) throw new Error(response.error?.message || "未能解除认领");
			setReleasing(null);
			await cache.invalidateQueries({ queryKey: ["peer-tasks", roomId] });
			await cache.invalidateQueries({ queryKey: ["peer-task-evidence", roomId, releasing.id] });
		} catch (e) {
			setReleaseError(e instanceof Error ? e.message : "未能解除认领");
			await cache.invalidateQueries({ queryKey: ["peer-tasks", roomId] });
		} finally {
			setBusy(false);
		}
	}

	async function add() {
		setBusy(true);
		setError("");
		try {
			const r = await apiClient.PUT("/api/v1/peer-rooms/{roomId}/tasks", {
				params: { path: { roomId } },
				body: {
					id: crypto.randomUUID(),
					goal,
					state: "open",
					owner: "",
					revision: 0,
					evidence: "",
					commit: "",
					reviewer: "",
				},
			});
			if (r.error) throw new Error("创建失败，请刷新核查，避免重复创建。");
			setGoal("");
			await cache.invalidateQueries({ queryKey: ["peer-tasks", roomId] });
		} catch (e) {
			setError(String(e));
		} finally {
			setBusy(false);
		}
	}
	return (
		<details className="border-b border-border p-4" onToggle={(e) => setExpanded(e.currentTarget.open)}>
			<summary className="cursor-pointer text-sm">
				团队任务（{query.data?.length ?? 0}）
			</summary>
			<p className="my-3 text-xs text-muted-foreground">
				成员可通过同伴工具提出并认领任务。这里只创建待办，不冒充成员认领，也不会自动启动模型。
			</p>
			{(error || query.isError) && (
				<p role="alert">{error || "任务读取失败，请稍后重试。"}</p>
			)}
			<ul className="space-y-3">
				{(query.data ?? []).map((t) => (
					<li key={t.id} className="border-b border-border pb-3">
						<p>{t.goal}</p>
						<p className="text-sm text-muted-foreground">
							{labels[t.state] ?? t.state} ·{" "}
							{t.owner ? label(t.owner) : "尚未认领"}
							{t.reviewer ? ` · ${t.reviewIssue ? "退回人" : "验收人"}：${label(t.reviewer)}` : ""}
						</p>
						{t.reviewIssue && <p className="mt-1 break-words text-sm text-muted-foreground">退回原因：{t.reviewIssue}</p>}
						<div className="mt-1 flex flex-wrap items-start justify-between gap-1">
							<PeerTaskEvidence roomId={roomId} taskId={t.id} label={label} />
							{t.state === "claimed" && !!epoch && (
								<Button size="sm" variant="ghost" disabled={busy} onClick={() => {
									setReleaseError("");
									setReleasing(t);
								}}>
									解除认领
								</Button>
							)}
						</div>
					</li>
				))}
			</ul>
			<form
				className="mt-3 flex flex-wrap gap-2"
				onSubmit={(e) => {
					e.preventDefault();
					if (goalValid && !busy) void add();
				}}
			>
				<label className="sr-only" htmlFor="peer-goal">
					任务目标
				</label>
				<input
					id="peer-goal"
					value={goal}
					onChange={(e) => setGoal(e.target.value)}
					placeholder="添加目标和验收标准，让成员自行认领"
					className="min-w-0 flex-1 rounded border border-border bg-secondary p-2"
				/>
				<Button
					type="submit"
					disabled={busy || !goalValid}
				>
					添加任务
				</Button>
			</form>
			<details className="mt-4">
				<summary>生成候选版本</summary>
				<p className="my-2 text-xs text-muted-foreground">
					{paused ? "恢复房间后可生成候选分支。" : readyForIntegration ? "按已验收的固定提交生成候选分支。" : "各项任务通过独立验收后可生成候选分支。"}
				</p>
				<label className="block text-sm">
					基线提交（完整提交编号）
					<input
						className="my-1 w-full rounded border border-border bg-secondary p-2"
						value={base}
						onChange={(e) => setBase(e.target.value.trim())}
					/>
				</label>
				<label className="block text-sm">
					候选名称
					<input
						className="my-1 w-full rounded border border-border bg-secondary p-2"
						value={name}
						maxLength={61}
						onChange={(e) => setName(e.target.value.trim())}
					/>
				</label>
				{name && !validCandidateName && <p className="mb-2 text-xs text-destructive">候选名称需以英文字母或数字开头，只可包含字母、数字和连字符。</p>}
				<Button
					disabled={
						busy ||
						paused ||
						!epoch ||
						!/^[a-f0-9]{40}$/i.test(base) ||
						!validCandidateName ||
						!readyForIntegration
					}
					onClick={integrate}
				>
					生成候选分支
				</Button>
				{integrationError && <p className="mt-2 break-words text-sm text-destructive" role="alert">{integrationError}</p>}
				{candidate && (
					<p className="mt-2 break-all" role="status">
						已生成待测试候选：{candidate}
					</p>
				)}
			</details>
			<PeerIntegrationEvidence roomId={roomId} epoch={epoch} active={expanded} />
			{releasing && (
				<ConfirmDialog
					open
					title="解除任务认领"
					description={`「${releasing.goal}」将回到待认领状态，交付与验收记录会保留。`}
					confirmLabel="放回待认领"
					busy={busy}
					error={releaseError}
					onConfirm={() => void release()}
					onOpenChange={(open) => { if (!open) setReleasing(null); }}
				/>
			)}
		</details>
	);
}
