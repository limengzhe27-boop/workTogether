import { hasActiveRoomTurns } from "../lib/peer-room-history";
import { peerDisplayName } from "../lib/peer-display-name";
import { peerRoomKey, usePeerRoom } from "../hooks/usePeerRoom";
import { useFileAttachments } from "../hooks/useFileAttachments";
import { X } from "lucide-react";
import { PeerAccessControl } from "./PeerAccessControl";
import {
	Plus,
	MoreHorizontal,
	ArrowUp,
	Paperclip,
 Image as ImageIcon,
 ChevronRight,
	UserPlus,
	Settings2,
	ListTodo,
	PanelsTopLeft,
	Pause,
	Play,
} from "lucide-react";
import { PeerAgentSettings, type PeerDeliveryProblem } from "./PeerAgentSettings";
import { PeerTimeline } from "./PeerTimeline";
import { PeerMemberPicker } from "./PeerMemberPicker";
import { useEffect, useRef, useState } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "../../api/schema";
import { apiClient } from "../lib/api-client";
import type { WorkspaceSession } from "../types/workspace";
import { Button } from "./ui/button";
import {
	OptionMenu,
	OptionMenuTrigger,
	OptionMenuContent,
	OptionMenuItem,
} from "./ui/option-menu";
import { Popover, PopoverTrigger, PopoverContent } from "./ui/popover";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";
import { PeerTasks, usePeerTasks } from "./PeerTasks";

type Room = components["schemas"]["PeerRoom"];
type Receipt = components["schemas"]["PeerBroadcastDelivery"];
const states: Record<string, string> = {
	accepted: "已接收，等待回复",
	pending: "已登记，接收结果待核查（不自动重发）",
	already_accepted: "之前已接收",
	blocked: "未发送",
	uncertain: "结果不确定，请核查",
};
const replyStates: Record<string, string> = {
	queued: "等待处理",
	running: "正在工作",
	completed: "本轮已回复",
	recovered: "已恢复",
	interrupted: "已中断",
	failed: "本轮失败",
	cancelled: "已取消",
};
function failure(value: unknown): Error {
	return new Error(
		typeof value === "object" && value !== null && "message" in value
			? String(value.message)
			: "操作失败，请刷新状态后检查；不要重复发送未确认的消息。",
	);
}

/** One durable project room; native sessions remain the owners of conversation history. */
export function PeerRoom({
	projectId,
	sessions,
	onManageSessions,
	onOpenSession,
}: {
	projectId: string;
	sessions: WorkspaceSession[];
	onManageSessions: () => void;
	onOpenSession?: (id: string) => void;
}) {
	const roomId = `project-${projectId}`;
	const files = useFileAttachments({ initialKey: `peer-room:${projectId}` });
	const filePicker = useRef<HTMLInputElement>(null);
	const sending = useRef(false);
	const cache = useQueryClient();
	const key = peerRoomKey(projectId);
	const [draftMembers, setDraftMembers] = useState<string[] | null>(null);
	const draftEpoch = useRef<number | null>(null);
	const [saveNotice, setSaveNotice] = useState("");
	const draftKey = `peer-room-draft:${roomId}`;
	const [text, setText] = useState(() => sessionStorage.getItem(draftKey) ?? "");
	function updateDraft(value: string) {
		setText(value);
		try {
			if (value) sessionStorage.setItem(draftKey, value);
			else sessionStorage.removeItem(draftKey);
		} catch {
			// The composer remains editable when browser storage is full.
		}
	}
	const [busy, setBusy] = useState(false);
	const [settingsBusy, setSettingsBusy] = useState(false);
	const [settingsOpen, setSettingsOpen] = useState(false);
	const [addingMembers, setAddingMembers] = useState(false);
 const [addMenuOpen, setAddMenuOpen] = useState(false);
	const [tasksOpen, setTasksOpen] = useState(false);
	const [error, setError] = useState("");
	const history = useRef<HTMLDivElement>(null);
	const historyContent = useRef<HTMLDivElement>(null);
	const followLatest = useRef(true);
	const [receipts, setReceipts] = useState<Receipt[]>([]);
	const recoveryKey = `peer-room-pending:${roomId}`;
	const [uncertain, setUncertain] = useState(
		() => sessionStorage.getItem(recoveryKey) === "1",
	);
	function markUncertain(value: boolean) {
		setUncertain(value);
		try {
			if (value) sessionStorage.setItem(recoveryKey, "1");
			else sessionStorage.removeItem(recoveryKey);
		} catch {
			// Keep the current composer's recovery guard when browser storage is full.
		}
	}
	const room = usePeerRoom(projectId, true, true);
	const members = room.data?.members ?? [];
	const selected = draftMembers ?? members;
	const membersDirty =
		selected.length !== members.length ||
		selected.some((id) => !members.includes(id));
	const pendingChanges = membersDirty;
	useEffect(() => {
		if (!pendingChanges) return;
		const warn = (event: BeforeUnloadEvent) => {
			event.preventDefault();
			event.returnValue = "";
		};
		window.addEventListener("beforeunload", warn);
		return () => window.removeEventListener("beforeunload", warn);
	}, [pendingChanges]);
	useEffect(() => {
		if (!pendingChanges) draftEpoch.current = null;
	}, [pendingChanges, room.data?.epoch]);
	const draftConflict =
		pendingChanges &&
		draftEpoch.current !== null &&
		draftEpoch.current !== (room.data?.epoch ?? 0);
	function editMembers(next: string[]) {
		if (draftEpoch.current === null) draftEpoch.current = room.data?.epoch ?? 0;
		setDraftMembers(next);
		setSaveNotice("");
	}
	function discardDraft() {
		setDraftMembers(null);
		draftEpoch.current = null;
		setSaveNotice("");
		setError("");
	}

	const eligible = sessions.filter(
		(s) => !s.hostId && !s.isTerminated && s.mode === "chat",
	);
	const eligibleIds = new Set(eligible.map((session) => session.id));
	const membershipChoices = [...new Set([...eligibleIds, ...members])];
	const deliveries = useQuery({
		queryKey: ["peer-room-history", roomId],
		enabled: !!room.data,
		retry: false,
		// Active turns stream through polling. Native session events wake quiet rooms;
		// the slow fallback also catches asynchronous deliveries and older daemons.
		refetchInterval: (query) => hasActiveRoomTurns(query.state.data) ? 2000 : 20000,
		queryFn: async () => {
			const result = await apiClient.GET(
				"/api/v1/peer-rooms/{roomId}/history",
				{ params: { path: { roomId } } },
			);
			if (result.error || !result.data) throw failure(result.error);
			return result.data;
		},
	});

	const bindings = useQueries({
		queries: members.map((sessionId) => ({
			queryKey: ["peer-binding", sessionId],
			retry: false,
			refetchInterval: 3000,
			queryFn: async () => {
				const r = await apiClient.GET(
					"/api/v1/sessions/{sessionId}/peer-binding",
					{ params: { path: { sessionId } } },
				);
				return r.error ? null : (r.data ?? null);
			},
		})),
	});
	async function bindTools() {
		if (!room.data) return;
		setBusy(true);
		setError("");
		try {
			const r = await apiClient.POST("/api/v1/peer-rooms/{roomId}/bind", {
				params: { path: { roomId } },
				body: { epoch: room.data.epoch },
			});
			if (r.error) throw failure(r.error);
			await cache.invalidateQueries({ queryKey: ["peer-binding"] });
		} catch (e) {
			setError(failure(e).message);
		} finally {
			setBusy(false);
		}
	}

	const tasks = usePeerTasks(roomId, !!room.data);
	const conversations = useQueries({
		queries: members.map((sessionId) => ({
			queryKey: ["peer-room-conversation", roomId, sessionId],
			retry: false,
			refetchInterval: 2000,
			queryFn: async () => {
				const result = await apiClient.GET(
					"/api/v1/sessions/{sessionId}/conversation",
					{ params: { path: { sessionId }, query: { limit: 1 } } },
				);
				if (result.error || !result.data) throw failure(result.error);
				return result.data;
			},
		})),
	});
	useEffect(() => {
		if (followLatest.current && history.current)
			history.current.scrollTop = history.current.scrollHeight;
	}, [deliveries.data]);
	useEffect(() => {
		const content = historyContent.current;
		const scroll = history.current;
		if (!content || !scroll || typeof ResizeObserver === "undefined") return;
		const observer = new ResizeObserver(() => {
			if (followLatest.current) scroll.scrollTop = scroll.scrollHeight;
		});
		observer.observe(content);
		return () => observer.disconnect();
	}, []);

	async function updateMemberSettings(
		id: string,
		setting: { disabled: boolean; peerLimit?: number | null },
	) {
		if (!room.data || pendingChanges) throw new Error("请先保存成员选择");
		await cache.cancelQueries({ queryKey: key });
		try {
			const result = await apiClient.PUT("/api/v1/peer-rooms/{roomId}", {
				params: { path: { roomId } },
				body: {
					...room.data,
					memberSettings: { ...room.data.memberSettings, [id]: setting },
				},
			});
			if (result.error || !result.data) throw failure(result.error);
			cache.setQueryData(key, result.data);
		} finally {
			await cache.invalidateQueries({ queryKey: key });
		}
	}
	const label = (id: string) => {
		const session = sessions.find((s) => s.id === id);
		return session ? peerDisplayName(session) : `会话 ${id}`;
	};
	async function configure(paused: boolean, nextMembers = selected) {
		setBusy(true);
		setError("");
		try {
			await cache.cancelQueries({ queryKey: key });
			const body: Room = {
				id: roomId,
				projectId,
				epoch: draftEpoch.current ?? room.data?.epoch ?? 0,
				members: nextMembers,
				paused,
				budget: room.data?.budget ?? 24,
				memberSettings: Object.fromEntries(
					Object.entries(room.data?.memberSettings ?? {}).filter(([id]) =>
						nextMembers.includes(id),
					),
				),
				used: room.data?.used ?? 0,
			};
			const result = await apiClient.PUT("/api/v1/peer-rooms/{roomId}", {
				params: { path: { roomId } },
				body,
			});
			if (result.error || !result.data) throw failure(result.error);
			cache.setQueryData(key, result.data);
			setDraftMembers(null);
			draftEpoch.current = null;
			setSaveNotice(
				`已保存 ${result.data.members.length} 位成员。下一次发送将连接当前房间同伴工具。`,
			);
			return true;
		} catch (e) {
			setError(failure(e).message);
			await cache.invalidateQueries({ queryKey: key });
			await cache.invalidateQueries({
				queryKey: ["peer-room-history", roomId],
			});
			return false;
		} finally {
			setBusy(false);
		}
	}
	async function send() {
		if (!room.data || !canSend || sending.current || files.hasPendingReads())
			return;
		sending.current = true;
		setBusy(true);
		setError("");
		setReceipts([]);
		markUncertain(true);
		followLatest.current = true;
		try {
			const result = await apiClient.POST(
				"/api/v1/peer-rooms/{roomId}/broadcast",
				{
					params: { path: { roomId } },
					body: {
						id: crypto.randomUUID(),
						epoch: room.data.epoch,
						message: text,
						attachments: files.getAttachments().flatMap((file) =>
							file.data
								? [
										{
											name: file.name,
											mimeType: file.mimeType,
											data: file.data,
										},
									]
								: [],
						),
						targets: availableMembers,
						connectPeers: true,
					},
				},
			);
			if (result.error || !result.data) {
				const errorCode = result.error && "code" in result.error ? result.error.code : undefined;
				if (
					result.response?.status === 400 ||
					result.response?.status === 413 ||
					(result.response?.status === 409 && errorCode === "PEER_BROADCAST_REJECTED")
				) {
					setError(failure(result.error).message);
					markUncertain(false);
					return;
				}
				throw failure(result.error);
			}
			const receipts = result.data.deliveries;
			if (!receipts.length) throw new Error("未收到任何成员的投递回执");
			setReceipts(receipts);
			// A blocked recipient received nothing. Keep the draft if every
			// recipient was blocked, but clear it after partial delivery.
			if (receipts.some((receipt) => receipt.state !== "blocked")) {
				updateDraft("");
				files.clear();
			}
			markUncertain(receipts.some((receipt) =>
				!["blocked", "accepted", "already_accepted"].includes(receipt.state),
			));
		} catch (e) {
			setError(failure(e).message);
			markUncertain(true);
		} finally {
			sending.current = false;
			setBusy(false);
			await cache.invalidateQueries({ queryKey: key });
			await cache.invalidateQueries({
				queryKey: ["peer-room-history", roomId],
			});
			await cache.invalidateQueries({
				queryKey: ["peer-room-conversation", roomId],
			});
		}
	}
	function memberIssue(index: number): string {
		const session = sessions.find((s) => s.id === members[index]);
		const query = conversations[index];
		if (room.data?.memberSettings?.[members[index]]?.disabled) return "已停用";
		if (!session || !eligibleIds.has(session.id))
			return "会话不可用，请在房间设置中移出并添加可用成员";
		if (query?.isError) return "会话状态读取失败，请刷新状态";
		if (!query?.data) return "正在检查会话状态";
		const state = query.data.controller;
		if (state === "stopped") return ""; // Explicit send restores this enabled native session.
		if (state === "connecting" || state === "recovering")
			return "会话连接或恢复中，请等待状态更新";
		if (!["ready", "busy", "hibernated"].includes(state ?? ""))
			return "会话状态未知，请检查原生会话";
		return "";
	}
	function deliveryStatus(d: {
		state: string;
		turnState?: string;
		replies?: { turnState: string; streaming: boolean }[];
	}) {
		const turn = d.turnState || d.replies?.at(-1)?.turnState;
		if (turn)
			return `${replyStates[turn] ?? turn}${d.state === "uncertain" ? "（投递回执仍待核查）" : ""}`;
		if (d.replies?.some((r) => r.streaming)) return "正在回复";
		return states[d.state] ?? d.state;
	}
	// The latest persisted delivery owns this label. The immediate receipt fills
	// the gap before history refreshes, and never replaces a newer saved delivery.
	const latestDeliveries = new Map((deliveries.data ?? []).map((d) => [d.target, d]));
	const recordedDeliveries = new Set(deliveries.data?.map((d) => d.id));
	const pendingReceipts = new Map(
		receipts.filter((r) => !recordedDeliveries.has(r.id)).map((r) => [r.session, r]),
	);
	function latestDeliveryProblem(id: string): PeerDeliveryProblem | undefined {
		const delivery = latestDeliveries.get(id);
		const receipt = pendingReceipts.get(id);
		if (receipt) {
			if (receipt.state === "blocked") return { status: "上次未送达", detail: receipt.detail };
			if (receipt.state === "uncertain") return { status: "上次送达未确认", detail: receipt.detail };
			return undefined;
		}
		if (delivery?.turnState === "failed")
			return { status: "上次已送达，处理失败", detail: "请查看对应消息的执行记录。" };
		if (delivery?.state === "blocked")
			return { status: "上次未送达", detail: delivery.detail };
		if (delivery?.state === "uncertain" && !delivery.turnState)
			return { status: "上次送达未确认", detail: delivery.detail };
		return undefined;
	}
	const allReceiptsBlocked =
		receipts.length > 0 && receipts.every((receipt) => receipt.state === "blocked");
	const unavailableMembers = members
		.map((id, i) => ({ id, issue: memberIssue(i) }))
		.filter((m) => m.issue);
	const availableMembers = members.filter((_, i) => !memberIssue(i));
	const canSend =
		!!room.data &&
		!room.data.paused &&
		members.length > 0 &&
		!room.isError &&
		!busy &&
		!settingsBusy &&
		!pendingChanges &&
		availableMembers.length > 0 &&
		!uncertain &&
		(!!text.trim() || files.attachments.length > 0) &&
		!files.preparing &&
		new TextEncoder().encode(text).length <= 256 * 1024;
	return (
		<section
			className="flex min-h-0 flex-1 flex-col overflow-hidden"
			aria-label="项目群聊"
		>
			<header className="flex shrink-0 flex-wrap items-center justify-between gap-3 px-5 py-2">
				<div>
					<h1 className="text-sm font-medium text-muted-foreground">
						项目群聊
					</h1>
				</div>
				<OptionMenu>
					<OptionMenuTrigger
						showCaret={false}
						aria-label="群聊菜单"
						title="群聊菜单"
						className="size-7 justify-center rounded-full p-0 text-muted-foreground"
					>
						<MoreHorizontal className="size-3.5" />
					</OptionMenuTrigger>
					<OptionMenuContent
						align="end"
						className="chat-settings-menu w-56 text-[12px]!"
					>
						<OptionMenuItem onSelect={() => setSettingsOpen(true)}>
							<Settings2 className="mr-2 size-3.5" />
							房间设置
						</OptionMenuItem>
						<OptionMenuItem
							disabled={!room.data}
							onSelect={() => setTasksOpen(true)}
						>
							<ListTodo className="mr-2 size-3.5" />
							团队任务
						</OptionMenuItem>
						<OptionMenuItem disabled={busy} onSelect={onManageSessions}>
							<PanelsTopLeft className="mr-2 size-3.5" />
							会话看板
						</OptionMenuItem>
						{room.data && (
							<OptionMenuItem
								disabled={busy || settingsBusy || pendingChanges || (room.data.paused && !members.length)}
								onSelect={() => void configure(!room.data!.paused, members)}
							>
								{room.data.paused ? (
									<Play className="mr-2 size-3.5" />
								) : (
									<Pause className="mr-2 size-3.5" />
								)}
								{room.data.paused ? "恢复房间" : "暂停房间"}
							</OptionMenuItem>
						)}
					</OptionMenuContent>
				</OptionMenu>
			</header>
			<div
				ref={history}
				onScroll={(e) => {
					const el = e.currentTarget;
					followLatest.current =
						el.scrollHeight - el.scrollTop - el.clientHeight < 80;
				}}
				className="min-h-0 flex-1 overflow-y-auto"
				aria-label="群聊内容"
			>

				<Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
					<DialogContent
						className="max-w-xl max-h-[80dvh] overflow-y-auto p-0"
						aria-describedby={undefined}
					>
						<DialogTitle className="px-4 pt-4 text-sm">群聊设置</DialogTitle>
						{error && (
							<p role="alert" className="px-4 text-sm text-destructive">
								{error}
							</p>
						)}

						<details
							className="border-b border-border p-4"
							open={
								!room.data || addingMembers || pendingChanges || !!saveNotice
							}
						>
							<summary className="cursor-pointer text-sm">
								已加入房间：{members.length} 位
								{pendingChanges ? ` · 未保存选择：${selected.length} 位` : ""}
							</summary>
							<Button
								disabled={
									busy ||
									settingsBusy ||
									draftConflict ||
									(!!room.data && !pendingChanges) ||
									room.isPending ||
									room.isError ||
									(selected.length === 0 && !room.data?.paused) ||
									selected.length > 16
								}
								onClick={() => configure(room.data?.paused ?? false)}
							>
								{room.data
									? pendingChanges
										? `保存选择（${selected.length} 位）`
										: "成员已保存"
									: "创建项目房间"}
							</Button>
							{pendingChanges && (
								<div
									role="status"
									className="my-3 rounded border border-border bg-secondary p-3 text-sm"
								>
									{draftConflict
										? "房间已被其他操作更新。你的选择仍保留，请重新载入已保存成员后再修改。"
										: `有未保存修改：已加入 ${members.length} 位，当前选择 ${selected.length} 位。请点击「保存选择」。保存前不会更改接收消息的成员。`}
									<Button
										variant="quiet"
										disabled={busy}
										onClick={discardDraft}
									>
										取消修改，载入已保存成员
									</Button>
								</div>
							)}
							{!pendingChanges && saveNotice && (
								<p role="status" className="my-3 text-sm">
									{saveNotice}
								</p>
							)}
							<div className="mt-3 flex flex-wrap gap-3">
								{membershipChoices.map((id) => (
									<label
										key={id}
										className="flex min-h-11 items-center gap-2 text-sm"
									>
										<input
											type="checkbox"
											checked={selected.includes(id)}
											disabled={busy || settingsBusy || (!eligibleIds.has(id) && !selected.includes(id))}
											onChange={(e) =>
												editMembers(
													e.target.checked
														? [...selected, id]
														: selected.filter((member) => member !== id),
												)
											}
										/>
										{label(id)}
										<span className="text-xs text-muted-foreground">
											{members.includes(id)
												? selected.includes(id)
													? eligibleIds.has(id) ? "已加入" : "会话不可用"
													: "待移出 · 未保存"
												: selected.includes(id)
													? "待加入 · 未保存"
													: "未加入"}
										</span>
									</label>
								))}
							</div>
							{!eligible.length && (
								<p className="my-2 text-sm">
									在输入框点击「＋」添加 Agent。
								</p>
							)}

							<p className="my-3 text-sm text-muted-foreground">
								{room.data?.paused ? "房间已暂停，可以清空成员。" : "开启的房间至少保留一位成员。"} 加入后通过本房间发送。
							</p>
						</details>
						{room.data && (
							<div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2">
								<Button
									variant="secondary"
									disabled={busy || settingsBusy || room.data.paused || pendingChanges}
									onClick={bindTools}
								>
									连接 / 更新同伴工具
								</Button>
								<p className="text-xs text-muted-foreground">
									{
										members.filter(
											(_, i) =>
												bindings[i]?.data?.roomId === roomId &&
												bindings[i]?.data?.epoch === room.data?.epoch,
										).length
									}{" "}
									/ {members.length} 位成员已授权当前房间。此操作不会启动模型。
								</p>
							</div>
						)}
						{unavailableMembers.length > 0 && (
							<div role="status" className="border-b border-border p-4 text-sm">
								<p>
									以下成员暂不可用，本次跳过。可用成员仍可接收新消息，不会自动恢复或补发。
								</p>
								{unavailableMembers.map(({ id, issue }) => (
									<div key={id} className="my-2">
										{label(id)}：{issue}
										{onOpenSession && sessions.some((session) => session.id === id) && (
											<Button variant="quiet" onClick={() => onOpenSession(id)}>
												打开原生会话检查
											</Button>
										)}
									</div>
								))}
							</div>
						)}
					</DialogContent>
				</Dialog>
				<Dialog open={tasksOpen} onOpenChange={setTasksOpen}>
					<DialogContent
						className="max-w-xl max-h-[80dvh] overflow-y-auto p-0"
						aria-describedby={undefined}
					>
						<DialogTitle className="px-4 pt-4 text-sm">团队任务</DialogTitle>

						{room.data && (
							<PeerTasks
								key={roomId}
								roomId={roomId}
								label={label}
								epoch={room.data.epoch}
								paused={room.data.paused}
							/>
						)}
					</DialogContent>
				</Dialog>
				{(error || room.isError) && (
					<p role="alert" className="p-4 text-destructive">
						{error || failure(room.error).message}
					</p>
				)}
				<div ref={historyContent} className="p-4" aria-label="成员对话记录">
					<div className="mx-auto max-w-3xl space-y-6">
						{deliveries.isError && (
							<p role="alert">群聊记录加载失败，请稍后刷新；不会重复发送。</p>
						)}
						{room.data === null && (
							<p className="py-12 text-center text-sm text-muted-foreground">
								用输入框旁的「＋」添加一位 Agent，即可开始对话。
							</p>
						)}
						{!deliveries.isPending &&
							!deliveries.isError &&
							!deliveries.data?.length && (
								<p className="py-12 text-center text-sm text-muted-foreground">
									说明目标，和项目成员一起开始。
								</p>
							)}
						<PeerTimeline
                        roomId={roomId}
                        onOpenSession={onOpenSession}
							deliveries={deliveries.data ?? []}
							tasks={deliveries.data ? tasks.data : undefined}
							label={label}
							provider={(id) => sessions.find((s) => s.id === id)?.provider}
							controls={room.data ? {
								epoch: room.data.epoch,
								paused: room.data.paused,
								members,
								disabledMembers: members.filter((id) => room.data?.memberSettings?.[id]?.disabled),
							} : undefined}
							steerSupported={(id) => conversations[members.indexOf(id)]?.data?.capabilities?.includes("steer")}
							status={deliveryStatus}
						/>
					</div>
				</div>
			</div>
			<footer className="max-h-[45%] shrink-0 overflow-y-auto px-4 pb-4 pt-2">
				<div className="mx-auto max-w-3xl">
					<div aria-live="polite">
						{receipts
							.filter((r) => r.state === "blocked" || r.state === "uncertain")
							.map((r) => (
								<p key={r.session} className="text-sm">
									{label(r.session)}：
									{deliveryStatus(
										deliveries.data?.find((d) => d.id === r.id) ?? r,
									)}
									{r.detail ? ` · ${r.detail}` : ""}
									{r.state === "uncertain" && r.id
										? ` · 投递编号：${r.id}`
										: ""}
								</p>
							))}
					</div>
					{allReceiptsBlocked && (
						<p role="status" className="my-2 text-sm">没有成员收到消息，草稿已保留。</p>
					)}
					{uncertain && !busy && (
						<div role="alert" className="my-2 text-sm">
							部分结果未确认。先检查成员记录，不会自动重发。
							<Button
								variant="secondary"
								onClick={() => {
									markUncertain(false);
									updateDraft("");
									files.clear();
								}}
							>
								已核查，撰写新消息
							</Button>
						</div>
					)}
					{busy && sending.current && (
						<p role="status" className="px-2 text-xs text-muted-foreground">
							正在连接成员并发送新消息…
						</p>
					)}
					{room.data?.paused && (
						<p role="status">
							{members.length ? "房间已暂停，请在群聊菜单恢复。" : "房间已暂停，添加成员后可恢复。"}
						</p>
					)}
					{!availableMembers.length && room.data && (
						<p role="status" className="mb-2 text-xs text-muted-foreground">
							{room.data && members.length === 0
								? "当前房间没有成员。点击「＋」添加 Agent。"
								: "没有可发送的成员。请启用成员或在房间设置中查看连接状态。"}
						</p>
					)}
					{pendingChanges && (
						<p role="status" className="mb-2 text-xs">
							成员选择尚未保存。
						</p>
					)}
					<form
						onDragOver={(e) => {
							if (e.dataTransfer.types.includes("Files")) e.preventDefault();
						}}
						onDrop={(e) => {
							if (!e.dataTransfer.files.length) return;
							e.preventDefault();
							if (!busy && !uncertain)
								void files.addFiles(Array.from(e.dataTransfer.files));
						}}
						className="rounded-[22px] border border-border/60 bg-secondary/70 p-2 transition-colors focus-within:border-border focus-within:bg-secondary motion-reduce:transition-none"
						onSubmit={(e) => {
							e.preventDefault();
							if (canSend) void send();
						}}
					>
						<input
							ref={filePicker}
							type="file"
							multiple
							className="sr-only"
							aria-label="添加文件或图片"
							disabled={busy || uncertain || files.preparing}
							onChange={(e) => {
								void files.addFiles(Array.from(e.target.files ?? []));
								e.target.value = "";
							}}
						/>
						{(files.attachments.length > 0 ||
							files.pendingAttachments.length > 0) && (
							<div
								className="flex flex-wrap gap-2 px-3 py-2"
								aria-label="待发送附件"
							>
								{files.attachments.some((file) => file.mimeType?.startsWith("image/")) && (
									<p className="w-full text-xs text-muted-foreground">
										图片随消息作为文件发送，由接收成员使用工具读取。
									</p>
								)}
								{files.attachments.map((file) => (
									<div
										key={file.id}
										className="flex max-w-56 items-center gap-2 rounded-lg border border-border px-2 py-1 text-xs"
									>
										{file.dataUrl ? (
											<img
												src={file.dataUrl}
												alt={file.name}
												className="size-9 rounded object-cover"
											/>
										) : (
											<Paperclip className="size-3.5 shrink-0" />
										)}
										<span className="truncate" title={file.name}>
											{file.name}
										</span>
										<button
											type="button"
											aria-label={`移除附件 ${file.name}`}
											disabled={busy || uncertain}
											onClick={() => files.remove(file.id)}
										>
											<X className="size-3" />
										</button>
									</div>
								))}
								{files.pendingAttachments.map((file) => (
									<span key={file.id} className="text-xs text-muted-foreground">
										正在读取 {file.name}…
									</span>
								))}
							</div>
						)}
						{files.error && (
							<p role="alert" className="px-3 py-1 text-xs text-destructive">
								{files.error}
							</p>
						)}
						<label className="sr-only" htmlFor="peer-message">
							群聊消息
						</label>
						<textarea
							id="peer-message"
							onPaste={(e) => {
								if (e.clipboardData.files.length && !busy && !uncertain) {
									e.preventDefault();
									void files.addFiles(Array.from(e.clipboardData.files));
								}
							}}
							rows={1}
							className="block min-h-10 w-full resize-none overflow-y-auto border-0 bg-transparent px-3 py-2 text-[length:var(--font-size-base)]! leading-5 outline-none placeholder:text-muted-foreground"
							style={{ fieldSizing: "content", maxHeight: "min(180px, 25dvh)" }}
							value={text}
							onChange={(e) => updateDraft(e.target.value)}
							onKeyDown={(e) => {
								if (
									e.key !== "Enter" ||
									e.shiftKey ||
									e.ctrlKey ||
									e.altKey ||
									e.metaKey ||
									e.nativeEvent.isComposing ||
									e.keyCode === 229 ||
									e.repeat
								)
									return;
								e.preventDefault();
								if (canSend) void send();
							}}
							placeholder="说明你希望团队完成什么…"
							disabled={busy || uncertain || settingsBusy}
						/>
						<div className="flex items-center gap-1 pt-1">
<Popover open={addMenuOpen} onOpenChange={value => {if (!busy) {setAddMenuOpen(value); if (!value) setAddingMembers(false);}}}>
 <PopoverTrigger asChild><button type="button" aria-label="添加" title="添加文件、图片或 Agent" disabled={busy || settingsBusy} className="inline-flex size-7 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-interactive-hover hover:text-foreground"><Plus className="size-3.5" /></button></PopoverTrigger>
 <PopoverContent side="top" align="start" aria-label="添加" className="w-60 max-w-[calc(100vw-24px)] max-h-[min(420px,var(--radix-popover-content-available-height))] overflow-y-auto p-1" onInteractOutside={e=>{if(busy)e.preventDefault();}} onEscapeKeyDown={e=>{if(busy)e.preventDefault();}}>
 {addingMembers ? (<PeerMemberPicker
								onBusyChange={setBusy}
								projectId={projectId}
								selectedCount={members.length}
								existingSessions={eligible.filter((session) => !members.includes(session.id))}
								onBack={() => setAddingMembers(false)}
 onClose={() => {setAddingMembers(false);setAddMenuOpen(false);}}
								onAdded={async (ids) => {
									const next = [...new Set([...members, ...ids])];
									if (!(await configure(room.data?.paused ?? false, next)))
										throw new Error("房间保存未确认");
									return "成员已加入房间。在下方查看接收人并发送新任务。";
								}}
							/>) : <div role="menu" aria-label="添加内容">
 <button type="button" role="menuitem" disabled={uncertain || files.preparing} onClick={()=>{setAddMenuOpen(false); if(filePicker.current){filePicker.current.accept="";filePicker.current.click();}}} className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-xs hover:bg-interactive-hover"><Paperclip className="size-4"/>添加文件</button>
 <button type="button" role="menuitem" disabled={uncertain || files.preparing} onClick={()=>{setAddMenuOpen(false); if(filePicker.current){filePicker.current.accept="image/*";filePicker.current.click();}}} className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-xs hover:bg-interactive-hover"><ImageIcon className="size-4"/>添加图片</button>
 <div className="my-1 border-t border-border/50"/>
 <button type="button" role="menuitem" disabled={room.isPending || room.isError || pendingChanges || members.length>=16} onClick={()=>setAddingMembers(true)} className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-xs hover:bg-interactive-hover"><UserPlus className="size-4"/>添加 Agent<ChevronRight className="ml-auto size-3.5 text-muted-foreground"/></button>
 </div>}
 </PopoverContent>
 </Popover>
							<PeerAccessControl
								sessions={members.flatMap((id) => {
									const session = sessions.find((s) => s.id === id);
									return session ? [{ ...session, title: label(id) }] : [];
								})}
								disabled={busy || settingsBusy}
								onBusyChange={setSettingsBusy}
							/>
							<div
								className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto"
								aria-label="项目成员"
							>
								{members.map((id, i) => {
									const session = sessions.find((s) => s.id === id);
									return session ? (
										<PeerAgentSettings
											key={id}
											session={{ ...session, title: label(id) }}
											issue={memberIssue(i)}
											deliveryProblem={latestDeliveryProblem(id)}
											onRefreshStatus={async () => {
												await Promise.all([conversations[i].refetch(), deliveries.refetch()]);
											}}
											usage={conversations[i]?.data?.usage}
                                            snapshot={conversations[i]?.data}
											disabled={busy || settingsBusy}
											onBusyChange={setSettingsBusy}
											memberSettings={room.data?.memberSettings?.[id]}
											onMemberSettingsChange={(setting) =>
												updateMemberSettings(id, setting)
											}
											removeDisabledReason={
                                                pendingChanges ? "请先保存或取消房间成员的未保存修改" :
                                                members.length === 1 && !room.data?.paused ? "群聊至少保留一位成员；可使用开关暂时停用" : undefined
                                            }
                                            onRemove={() => configure(room.data?.paused ?? false, members.filter((member) => member !== id))}
                                            onRenamed={(name) => void name}
											onOpenSession={
												onOpenSession ? () => onOpenSession(id) : undefined
											}
										/>
									) : null;
								})}
							</div>

							<button
								type="submit"
								aria-label="发送消息"
								title={`发送给 ${availableMembers.length} 位已启用成员，自动连接`}
								disabled={!canSend}
								className="inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-foreground text-background hover:opacity-80 focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-30"
							>
								<ArrowUp className="size-4" />
							</button>
						</div>
						{new TextEncoder().encode(text).length > 256 * 1024 && (
							<p role="alert" className="p-2 text-xs text-destructive">
								消息超过 256 KB，请将较大的内容作为文件添加。
							</p>
						)}
					</form>
					<p className="mt-1.5 text-center text-[11px] text-muted-foreground/70">
						回车发送 · ⇧＋回车换行
					</p>
				</div>
			</footer>
		</section>
	);
}
