import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { GeneratedImage } from "./chat/GeneratedImage";
import { CopyButton } from "./chat/CopyButton";
import { PeerTaskEvidence } from "./PeerEvidence";
import { PeerExecution } from "./PeerExecution";
import type { components } from "../../api/schema";
import { ChatMarkdown } from "./chat/ChatMarkdown";
import { AgentAvatar } from "./AgentAvatar";
type Task = components["schemas"]["PeerTask"];
type Delivery = components["schemas"]["PeerDelivery"];
export function peerMessageGroups(deliveries: Delivery[]) {
	const groups: Delivery[][] = [];
	const indexes = new Map<string, number>();
	for (const d of deliveries) {
		const key =
			d.broadcastId && !d.sender
				? JSON.stringify([
						d.broadcastId,
						d.epoch,
						d.message,
						d.attachments ?? [],
					])
				: d.id;
		const index = indexes.get(key);
		if (index === undefined) {
			indexes.set(key, groups.length);
			groups.push([d]);
		} else groups[index].push(d);
	}
	return groups;
}
// Native message creation time survives refresh and does not change with streaming updates.
export function orderPeerReplies(group: Delivery[]) {
	const firstReply = (d: Delivery) => {
		const visible = (d.replies ?? []).filter((r) => r.text.trim());
		const times = visible
			.map((r) => Date.parse(r.createdAt))
			.filter(Number.isFinite);
		return {
			replied: visible.length > 0,
			time: times.length ? Math.min(...times) : Infinity,
		};
	};
	return group
		.map((delivery, index) => ({ delivery, index, ...firstReply(delivery) }))
		.sort(
			(a, b) =>
				Number(b.replied) - Number(a.replied) ||
				a.time - b.time ||
				a.index - b.index,
		)
		.map(({ delivery }) => delivery);
}
export function PeerTimeline({
	deliveries,
	tasks = [],
	label,
	provider,
	status,
	controls,
	steerSupported,
	roomId,
	onOpenSession,
}: {
	deliveries: Delivery[];
	tasks?: Task[];
	roomId?: string;
	controls?: { epoch: number; paused: boolean; members: readonly string[]; disabledMembers: readonly string[] };
	onOpenSession?: (id: string) => void;
	label: (id: string) => string;
	provider: (id: string) => string | undefined;
	steerSupported?: (id: string) => boolean | undefined;
	status: (d: Delivery) => string;
}) {
	const { t } = useTranslation();
	const tasksByDelivery = new Map<string, Task[]>();
	const withReplies = new Set(
		deliveries
			.filter((d) => d.replies?.some((reply) => reply.text.trim()))
			.map((d) => d.id),
	);
	const unlinked: Task[] = [];
	for (const task of tasks) {
		if (!task.deliveryId || !withReplies.has(task.deliveryId)) {
			if (
				task.state === "submitted" ||
				task.state === "accepted" ||
				task.deliveryId ||
				task.reviewIssue
			)
				unlinked.push(task);
			continue;
		}
		const linked = tasksByDelivery.get(task.deliveryId) ?? [];
		linked.push(task);
		tasksByDelivery.set(task.deliveryId, linked);
	}
	return (
		<div className="space-y-8 select-text" aria-label="群聊消息记录">
			{peerMessageGroups(deliveries).map((group) => {
				const first = group[0];
				return (
					<div key={first.id} className="space-y-5">
						<div
							className={
								first.sender
									? "text-sm text-muted-foreground"
									: "ml-auto max-w-[85%] rounded-2xl bg-secondary px-5 py-3"
							}
						>
							<div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
								{first.sender && (
									<AgentAvatar
										provider={provider(first.sender) ?? ""}
										className="size-4 shrink-0"
										decorative
									/>
								)}
								<span>
									{first.sender
										? `${label(first.sender)} → ${label(first.target)}`
										: "你"}
								</span>
							</div>
							<ChatMarkdown text={first.message} />
							{first.attachments?.length ? (
								<ul
									aria-label="本次附件"
									className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground"
								>
									{first.attachments.map((file, i) => (
										<li
											key={`${file.sha256}-${i}`}
											className="rounded-md border border-border px-2 py-1"
											title={t("peer.attachmentHash", { digest: file.sha256 })}
										>
											{file.name || "附件"} · {t("peer.attachmentSize", { size: Math.ceil(file.size / 1024) })}
										</li>
									))}
								</ul>
							) : null}
						</div>
						{orderPeerReplies(group).map((d) => (
							<article
								key={d.id}
								className="py-1"
								aria-label={`来自 ${label(d.target)} 的回复`}
							>
								<div className="mb-3 flex items-center gap-2 text-xs text-muted-foreground">
									<AgentAvatar
										provider={provider(d.target) ?? ""}
										className="size-4 shrink-0"
										decorative
									/>
									<span className="font-medium text-foreground">
										{label(d.target)}
									</span>
									<span>{status(d)}</span>
								</div>
								{d.detail && (
									<p role="status" className="mb-3 text-sm text-destructive">
										{d.detail}
									</p>
								)}
								{d.state === "uncertain" && (
									<details className="mb-3 text-xs text-muted-foreground">
										<summary className="cursor-pointer">查看投递异常</summary>
										<p>{d.detail || "旧记录未保存失败原因；不会自动重发。"}</p>
										<p>投递编号：{d.id}</p>
									</details>
								)}
								<PeerReplies replies={d.replies ?? []}>
									{d.generatedImages?.length ? (
										<div className="flex flex-wrap gap-3">
											{d.generatedImages.map((image) => <GeneratedImage key={image.id} {...image} />)}
										</div>
									) : null}
								</PeerReplies>
								{roomId &&
									d.replies?.some((reply) => reply.text.trim()) &&
									tasksByDelivery
										.get(d.id)
										?.map((task) => (
											<PeerDeliveryCard
												key={task.id}
												roomId={roomId}
												task={task}
												label={label}
											/>
										))}
								{roomId && (
									<PeerExecution
										roomId={roomId}
										deliveryId={d.id}
										watch={
											d.turnState === "running" ||
											d.replies?.some((reply) => reply.streaming)
										}
										sessionId={d.target}
										roomEpoch={controls?.epoch}
										controlEnabled={controls?.members.includes(d.target)}
										steerPaused={controls?.paused || controls?.disabledMembers.includes(d.target)}
										steerSupported={steerSupported?.(d.target)}
										onOpenSession={onOpenSession}
									/>
								)}
								{d.repliesTruncated && (
									<p className="text-xs text-muted-foreground">
										更多内容请查看原生会话。
									</p>
								)}
							</article>
						))}
					</div>
				);
			})}
			{roomId && unlinked.length > 0 && (
				<section aria-label="未关联回复的交付记录">
					<p className="text-xs text-muted-foreground">交付记录 · 无对应回复</p>
					{unlinked.map((task) => (
						<PeerDeliveryCard
							key={task.id}
							roomId={roomId}
							task={task}
							label={label}
						/>
					))}
				</section>
			)}
		</div>
	);
}

/** Earlier updates remain available, while the latest answer stays readable during streaming. */
function PeerReplies({
	replies,
	children,
}: {
	replies: NonNullable<Delivery["replies"]>;
	children?: ReactNode;
}) {
	const visible = replies.filter((r) => r.text.trim());
	const latest = visible.at(-1);
	if (!latest) return children;
	const earlier = visible.slice(0, -1);
	return (
		<div className="space-y-3 text-base leading-7">
			{earlier.length > 0 && (
				<details className="text-sm text-muted-foreground">
					<summary className="cursor-pointer select-none py-1">
						查看过程 · {earlier.length} 条更新
					</summary>
					<div className="mt-2 space-y-3 border-l border-border pl-3">
						{earlier.map((r) => (
							<div key={r.id}>
								<ChatMarkdown text={r.text} streaming={r.streaming} />
								<CopyButton
									text={r.text}
									label="复制整条回复"
									copiedLabel="已复制"
									compact
									size="icon-xs"
								/>
							</div>
						))}
					</div>
				</details>
			)}
			<ChatMarkdown
				key={latest.id}
				text={latest.text}
				streaming={latest.streaming}
			/>
			{children}
			<div className="flex items-center text-muted-foreground">
				<CopyButton
					text={latest.text}
					label="复制整条回复"
					copiedLabel="已复制"
					compact
					size="icon-xs"
				/>
			</div>
		</div>
	);
}

function PeerDeliveryCard({
	roomId,
	task,
	label,
}: {
	roomId: string;
	task: Task;
	label: (id: string) => string;
}) {
	const statusByState: Record<string, string> = {
		open: "已放回待认领",
		claimed: "进行中",
		submitted: "待独立验收",
		accepted: "验收通过",
	};
	const result = task.reviewIssue ? "验收未通过" : statusByState[task.state] ?? task.state;
	const hasSubmission = task.state === "submitted" || task.state === "accepted" || !!task.reviewIssue;
	return (
		<section
			aria-label={`任务交付：${task.goal}`}
			className="my-3 w-full max-w-lg rounded-xl border border-border bg-secondary/30 px-3 py-2 text-xs"
		>
			<div className="flex items-start justify-between gap-3">
				<p
					className="line-clamp-2 break-words text-sm font-medium text-foreground"
					title={task.goal}
				>
					{task.goal}
				</p>
				<span className="shrink-0 text-muted-foreground" aria-live="polite">
					{result}
				</span>
			</div>
			{task.owner && (
				<p className="mt-1 text-muted-foreground">
					{hasSubmission ? `${label(task.owner)} 提交` : `${label(task.owner)} 认领`}
					{task.reviewer && (task.state === "accepted" || task.reviewIssue)
						? ` · ${label(task.reviewer)} 验收`
						: ""}
				</p>
			)}
			{task.reviewIssue && (
				<p
					className="mt-1 line-clamp-1 break-all text-destructive"
					title={task.reviewIssue}
				>
					{task.reviewIssue}
				</p>
			)}
			<div className="mt-1 text-muted-foreground">
				<PeerTaskEvidence
					roomId={roomId}
					taskId={task.id}
					label={label}
					linkedDelivery={
						task.deliveryId && task.owner && hasSubmission
							? { id: task.deliveryId, owner: task.owner }
							: undefined
					}
					compact
				/>
			</div>
		</section>
	);
}
