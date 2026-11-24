import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../lib/api-client";
import { toActivity } from "../hooks/useConversation";
import type { ConversationActivity } from "../types/conversation";
import { ActivityRow } from "./chat/ChatTimelineItems";

function needsApproval(activity: ConversationActivity) {
	return activity.activityKind === "approval" && activity.status === "pending";
}

function executionPriority(activity: ConversationActivity) {
	if (needsApproval(activity)) return 0;
	if (activity.activityKind === "error" || activity.status === "failed") return 1;
	return 2;
}

/** Native records and scoped controls for an individual delivery. */
export function PeerExecution({
	roomId,
	deliveryId,
	sessionId,
	watch = false,
	roomEpoch,
	controlEnabled = true,
	steerPaused = false,
	steerSupported,
	onOpenSession,
}: {
	roomId: string;
	deliveryId: string;
	sessionId: string;
	watch?: boolean;
	roomEpoch?: number;
	controlEnabled?: boolean;
	steerPaused?: boolean;
	steerSupported?: boolean;
	onOpenSession?: (id: string) => void;
}) {
	const [open, setOpen] = useState(false);
	const [busy, setBusy] = useState(false);
	const [notice, setNotice] = useState("");
	const [guidance, setGuidance] = useState("");
	const [stopRequested, setStopRequested] = useState(false);
	const [submittedApprovals, setSubmittedApprovals] = useState<string[]>([]);
	const [submittedControl, setSubmittedControl] = useState<{
		action: string;
		requestId?: string;
	}>();
	const query = useQuery({
		queryKey: ["peer-execution", roomId, deliveryId],
		enabled: open || watch,
		retry: false,
		refetchInterval: (q) =>
			(open || watch) && (!q.state.data || q.state.data.state === "running")
				? 3000
				: false,
		queryFn: async () => {
			const r = await apiClient.GET(
				"/api/v1/peer-rooms/{roomId}/deliveries/{deliveryId}/execution",
				{ params: { path: { roomId, deliveryId } } },
			);
			if (r.error || !r.data)
				throw new Error("尚无可关联的原生执行记录；不能据此判断成功或失败。");
			return r.data;
		},
	});
	const canControl = controlEnabled && query.data?.state === "running" && !!query.data.providerTurnId;
	const steerBlocked = steerPaused || (roomEpoch !== undefined && query.data?.epoch !== roomEpoch);
	const canSteer = canControl && !steerBlocked && steerSupported;
	async function control(
		action: "stop" | "steer" | "approve",
		requestId?: string,
		decision?: string,
	) {
		if (
			!query.data || !canControl || busy ||
			(action === "stop" && stopRequested) ||
			(action === "steer" && !canSteer) ||
			(action === "approve" && (!requestId || submittedApprovals.includes(requestId)))
		) return;
		setBusy(true);
		setNotice("");
		setSubmittedControl(undefined);
		try {
			const r = await apiClient.POST(
				"/api/v1/peer-rooms/{roomId}/deliveries/{deliveryId}/control",
				{
					params: { path: { roomId, deliveryId } },
					body: {
						epoch: roomEpoch ?? query.data.epoch,
						conversationId: query.data.conversationId,
						turnId: query.data.turnId,
						action,
						id: crypto.randomUUID(),
						text: guidance,
						requestId,
						decision,
					},
				},
			);
			if (r.data?.status === "uncertain") {
				setNotice("上次操作结果不确定，未重复执行。请核查原生会话。");
				return;
			}
			const expected = action === "stop" ? "stop_requested" : action === "steer" ? "steered" : "decision_submitted";
			if (r.error || r.data?.status !== expected) {
				setNotice(r.error?.message || "未收到操作回执，请核查原生会话。");
				return;
			}
			setSubmittedControl({ action, requestId });
			if (action === "stop") setStopRequested(true);
			if (action === "approve" && requestId)
				setSubmittedApprovals((previous) => [...previous, requestId]);
			setNotice(
				action === "stop"
					? "停止请求已提交，等待客户端确认停止。"
					: action === "steer"
						? "指令已追加至指定运行。"
						: "审批决定已提交，等待状态更新。",
			);
			if (action === "steer") setGuidance("");
			await query.refetch();
		} catch {
			setNotice("操作结果不确定，请核查原生会话，不会自动重试。");
		} finally {
			setBusy(false);
		}
	}
	const confirmedApproval =
		submittedControl?.action === "approve" &&
		query.data?.activities.find(
			(a) =>
				a.requestId === submittedControl.requestId &&
				a.activityKind === "approval" &&
				a.status === "resolved",
		);
	const displayedNotice = confirmedApproval
		? "审批结果已记录。"
		: submittedControl?.action === "stop" && query.data?.state === "interrupted"
			? "客户端已确认本次运行中断。"
			: submittedControl?.action === "stop" &&
				  query.data?.state &&
				  query.data.state !== "running"
				? "本次运行已结束，实际结果见下方记录。"
				: notice;
	const activities = (query.data?.activities ?? [])
		.map(toActivity)
		.filter((activity) => activity.activityKind !== "system" || activity.detail?.event !== "image.generated");
	const reasoning = activities.filter((a) => a.activityKind === "reasoning");
	const execution = activities
		.filter((a) => a.activityKind !== "reasoning")
		.sort((a, b) => executionPriority(a) - executionPriority(b));
	const approvals = canControl ? execution.filter(needsApproval).length : 0;
	const failures = execution.filter((a) => executionPriority(a) === 1).length;
	return (
		<details
			className="mt-3 text-xs text-muted-foreground"
			onToggle={(e) => {
				if (e.target === e.currentTarget) setOpen(e.currentTarget.open);
			}}
		>
			<summary className="cursor-pointer py-1">
				<span>执行记录</span>
				{approvals > 0 && (
					<span className="ml-2 text-warning">待你审批 {approvals} 项</span>
				)}
				{failures > 0 ? (
					<span className="ml-2 text-destructive">执行异常 {failures} 项</span>
				) : query.data?.state === "failed" ? (
					<span className="ml-2 text-destructive">运行失败</span>
				) : null}
			</summary>
			{open && (
				<div className="space-y-3 border-l border-border pl-3">
					{query.isLoading && <p>正在读取原生记录…</p>}
					{query.isError && <p role="status">{query.error.message}</p>}
					{query.data && (
						<>
							{displayedNotice && <p role="status">{displayedNotice}</p>}
							{canControl && (
								<div className="space-y-2">
									<button
										type="button"
										disabled={busy || stopRequested}
										className="rounded-md border border-border px-2 py-1 text-foreground hover:bg-muted disabled:opacity-50"
										onClick={() => control("stop")}
									>
										停止本次运行
									</button>
									{canSteer ? (
										<div className="flex gap-2">
											<input
												className="min-w-0 flex-1 rounded-md border border-border bg-transparent px-2 py-1 text-foreground"
												aria-label="给本次运行追加指令"
												value={guidance}
												onChange={(e) => setGuidance(e.target.value)}
												placeholder="补充本次运行的指令"
												maxLength={2000}
											/>
											<button
												type="button"
												className="shrink-0 rounded-md border border-border px-2 py-1 text-foreground hover:bg-muted disabled:opacity-50"
												disabled={busy || !guidance.trim()}
												onClick={() => control("steer")}
											>
												追加
											</button>
										</div>
									) : (
										<p>
											{steerBlocked
												? "房间或成员状态已变化，本轮只能停止或处理已有审批。"
												: steerSupported === false
													? "当前客户端未声明支持运行中追加指令。"
													: "尚未确认此会话是否支持运行中追加指令。"}
										</p>
									)}
								</div>
							)}
							{query.data.state === "interrupted" && <p>本次运行已中断</p>}
							{query.data.state === "cancelled" && <p>本次运行已取消</p>}
							{!execution.length && <p>本轮暂无可显示的工具或审批记录。</p>}
							{execution.map((activity) => (
								<div key={activity.id}>
									{canControl && needsApproval(activity) && (
										<p className="text-warning">等待你审批</p>
									)}
									{activity.activityKind === "approval" &&
										activity.status === "resolved" && (
											<p className="text-foreground">
												{resolvedApprovalLabel(activity)}
											</p>
										)}
									<ActivityRow activity={activity} />
									{canControl && needsApproval(activity) &&
										activity.requestId &&
										activity.decisions?.map((decision) => (
											<button
												key={decision.id}
												type="button"
												disabled={busy || submittedApprovals.includes(activity.requestId ?? "")}
												className="mr-2 underline"
												onClick={() => control("approve", activity.requestId, decision.id)}
											>
												{decision.label}
											</button>
										))}
								</div>
							))}
							{reasoning.length > 0 && (
								<details>
									<summary className="cursor-pointer py-1">思考过程</summary>
									<div className="mt-2 space-y-2">
										{reasoning.map((activity) => (
											<ActivityRow key={activity.id} activity={activity} />
										))}
									</div>
								</details>
							)}
							{query.data.truncated && (
								<p>最多显示 200 条事件，待审批优先。更多记录可在原生会话查看。</p>
							)}
						</>
					)}
					{onOpenSession && (
						<button
							type="button"
							className="underline"
							onClick={() => onOpenSession(sessionId)}
						>
							打开原生会话查看详情
						</button>
					)}
				</div>
			)}
		</details>
	);
}

function resolvedApprovalLabel(
	activity: ReturnType<typeof toActivity>,
): string {
	const detail = activity.detail as { decision?: string } | undefined;
	const choice = activity.decisions?.find((d) => d.id === detail?.decision);
	if (choice?.kind === "reject_once" || choice?.kind === "reject_always")
		return "审批已拒绝";
	if (choice?.kind === "allow_once" || choice?.kind === "allow_always")
		return `审批已批准：${choice.label}`;
	return choice
		? `审批已处理：${choice.label}`
		: "审批已处理（客户端未提供决定说明）";
}
