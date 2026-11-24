import { PeerIntegrationEvidence, PeerTaskEvidence } from "./PeerEvidence";
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { PeerExecution } from "./PeerExecution";
import { imageSupport } from "./PeerCapabilities";
import type { components } from "../../api/schema";
const api = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../lib/api-client", () => ({ apiClient: api }));
vi.mock("./chat/ChatTimelineItems", () => ({
	ActivityRow: ({ activity }: { activity: { summary: string } }) => (
		<p>{activity.summary}</p>
	),
}));
const clients: QueryClient[] = [];
afterEach(() => {
	cleanup();
	clients.forEach((c) => c.clear());
	vi.resetAllMocks();
});
function openDetails(el: HTMLDetailsElement, _opts?: unknown) {
	el.open = true;
	fireEvent(el, new Event("toggle"));
}
function mount(watch = false, steerSupported?: boolean, roomEpoch?: number, steerPaused?: boolean) {
	const c = new QueryClient({ defaultOptions: { queries: { retry: false } } });
	clients.push(c);
	return render(
		<QueryClientProvider client={c}>
			<section aria-label="成员 A">
				<PeerExecution roomId="room" deliveryId="da" sessionId="a" watch={watch} steerSupported={steerSupported} roomEpoch={roomEpoch} steerPaused={steerPaused} />
			</section>
			<section aria-label="成员 B">
				<PeerExecution roomId="room" deliveryId="db" sessionId="b" />
			</section>
		</QueryClientProvider>,
	);
}
function records(id: string) {
	return {
		sessionId: id,
		epoch: 3,
		conversationId: `conv-${id}`,
		turnId: `turn-${id}`,
		providerTurnId: `native-${id}`,
		state: "running",
		truncated: false,
		activities: [
			{
				kind: "activity",
				id: `tool-${id}`,
				sequence: 1,
				revision: 1,
				activityKind: "command",
				status: "completed",
				summary: `command ${id}`,
				createdAt: "2026-11-13T00:00:00Z",
				detail: { exitCode: 0 },
			},
		],
	};
}
it("loads only the opened delivery, keeps members separate and restores without duplicates", async () => {
	api.GET.mockImplementation((_url, opts) =>
		Promise.resolve({ data: records(opts.params.path.deliveryId) }),
	);
	const view = mount();
	expect(api.GET).not.toHaveBeenCalled();
	openDetails(
		within(screen.getByRole("region", { name: "成员 A" }))
			.getByText("执行记录")
			.closest("details")!,
		{ target: { open: true } },
	);
	expect(await screen.findByText("command da")).toBeVisible();
	expect(screen.queryByText("command db")).toBeNull();
	openDetails(
		within(screen.getByRole("region", { name: "成员 B" }))
			.getByText("执行记录")
			.closest("details")!,
		{ target: { open: true } },
	);
	expect(await screen.findByText("command db")).toBeVisible();
	expect(screen.getAllByText("command da")).toHaveLength(1);
	expect(api.POST).not.toHaveBeenCalled();
	view.unmount();
	mount();
	openDetails(
		within(screen.getByRole("region", { name: "成员 A" }))
			.getByText("执行记录")
			.closest("details")!,
		{ target: { open: true } },
	);
	expect(await screen.findByText("command da")).toBeVisible();
	expect(screen.getAllByText("command da")).toHaveLength(1);
});
it("shows in-run guidance only for a conversation that advertises it", async () => {
	api.GET.mockResolvedValue({ data: records("a") });
	mount(false, false);
	openDetails(screen.getAllByText("执行记录")[0].closest("details")!);
	expect(await screen.findByText("当前客户端未声明支持运行中追加指令。")).toBeVisible();
	expect(screen.queryByRole("textbox", { name: "给本次运行追加指令" })).toBeNull();
	expect(screen.getByRole("button", { name: "停止本次运行" })).toBeEnabled();
	cleanup();
	mount(false, true);
	openDetails(screen.getAllByText("执行记录")[0].closest("details")!);
	expect(await screen.findByRole("textbox", { name: "给本次运行追加指令" })).toBeVisible();
	expect(api.POST).not.toHaveBeenCalled();
});
it("keeps generated media out of the collapsible execution record", async () => {
	const data = records("a");
	const activity = data.activities[0];
	const image = { ...activity, id: "image", activityKind: "system", summary: "Generated image", detail: { event: "image.generated" } };
	api.GET.mockResolvedValue({ data: { ...data, activities: [activity, image] } });
	mount();
	openDetails(screen.getAllByText("执行记录")[0].closest("details")!);
	expect(await screen.findByText("command a")).toBeVisible();
	expect(screen.queryByText("Generated image")).toBeNull();
});

it("surfaces blockers while closed and keeps reasoning behind a separate disclosure", async () => {
	const data = records("a");
	const command = data.activities[0];
	data.activities = [
		command,
		{ ...command, id: "reason", activityKind: "reasoning", summary: "draft thoughts" },
		{ ...command, id: "failure", status: "failed", summary: "failed command" },
		{ ...command, id: "approval", activityKind: "approval", status: "pending", summary: "requires approval" },
	];
	api.GET.mockResolvedValue({ data });
	mount(true);
	const region = within(screen.getByRole("region", { name: "成员 A" }));
	const waiting = await region.findByText("待你审批 1 项");
	expect(waiting).toBeVisible();
	expect(region.getByText("执行异常 1 项")).toBeVisible();
	expect(region.queryByText("draft thoughts")).toBeNull();
	expect(api.GET).toHaveBeenCalledTimes(1);
	openDetails(waiting.closest("details")!);
	const approval = region.getByText("requires approval");
	const failure = region.getByText("failed command");
	expect(approval.compareDocumentPosition(failure) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	expect(failure.compareDocumentPosition(region.getByText("command a")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	const thought = region.getByText("draft thoughts");
	expect(thought).not.toBeVisible();
	openDetails(region.getByText("思考过程").closest("details")!);
	expect(thought).toBeVisible();
	expect(approval).toBeVisible();
});

it("targets an exact run and reports requested stop without claiming stopped or retrying", async () => {
	api.GET.mockResolvedValue({ data: records("a") });
	api.POST.mockResolvedValue({ data: { status: "stop_requested" } });
	mount();
	openDetails(screen.getAllByText("执行记录")[0].closest("details")!, {
		target: { open: true },
	});
	fireEvent.click(await screen.findByText("停止本次运行"));
	await screen.findByText("停止请求已提交，等待客户端确认停止。");
	expect(api.POST).toHaveBeenCalledTimes(1);
	expect(api.POST.mock.calls[0][1]).toMatchObject({
		params: { path: { roomId: "room", deliveryId: "da" } },
		body: {
			action: "stop",
			conversationId: "conv-a",
			turnId: "turn-a",
			epoch: 3,
		},
	});
});
it("stops or approves an exact run after a room update and prevents steering or duplicate control", async () => {
	const data = records("a");
	data.activities = [{
		...data.activities[0],
		activityKind: "approval",
		status: "pending",
		requestId: "request-a",
		detail: { decisions: [{ id: "deny", label: "拒绝" }] },
	}] as typeof data.activities;
	api.GET.mockResolvedValue({ data });
	api.POST
		.mockResolvedValueOnce({ data: { status: "decision_submitted" } })
		.mockResolvedValueOnce({ data: { status: "stop_requested" } });
	mount(false, true, 5, true);
	openDetails(screen.getAllByText("执行记录")[0].closest("details")!);

	fireEvent.click(await screen.findByRole("button", { name: "拒绝" }));
	await screen.findByText("审批决定已提交，等待状态更新。");
	expect(screen.getByRole("button", { name: "拒绝" })).toBeDisabled();
	expect(screen.queryByRole("textbox", { name: "给本次运行追加指令" })).toBeNull();

	fireEvent.click(screen.getByRole("button", { name: "停止本次运行" }));
	await screen.findByText("停止请求已提交，等待客户端确认停止。");
	expect(screen.getByRole("button", { name: "停止本次运行" })).toBeDisabled();
	expect(screen.getByRole("button", { name: "拒绝" })).toBeDisabled();
	expect(api.POST).toHaveBeenCalledTimes(2);
	expect(api.POST.mock.calls.map(([, { body }]) => body)).toEqual([
		expect.objectContaining({ epoch: 5, conversationId: "conv-a", turnId: "turn-a", action: "approve" }),
		expect.objectContaining({ epoch: 5, conversationId: "conv-a", turnId: "turn-a", action: "stop" }),
	]);
});

it("failed controls are not replayed and only offered approval choices are rendered", async () => {
	const data = records("a");
	data.activities = [
		{
			...data.activities[0],
			activityKind: "approval",
			status: "pending",
			summary: "需审批",
			requestId: "request-a",
			detail: { decisions: [{ id: "deny", label: "拒绝" }] },
		} as any,
	];
	api.GET.mockResolvedValue({ data });
	api.POST.mockResolvedValue({ error: { message: "请求已失效" } });
	mount();
	openDetails(screen.getAllByText("执行记录")[0].closest("details")!, {
		target: { open: true },
	});
	fireEvent.click(await screen.findByRole("button", { name: "拒绝" }));
	await screen.findByText("请求已失效");
	expect(screen.queryByRole("button", { name: "允许" })).toBeNull();
	await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
	expect(api.POST.mock.calls[0][1].body).toMatchObject({
		action: "approve",
		requestId: "request-a",
		decision: "deny",
	});
});
it("does not turn absent metadata into support or model verification", () => {
	type S = components["schemas"]["ConversationSnapshotResponse"];
	expect(imageSupport()).toContain("未知");
	expect(
		imageSupport({ controller: "ready", capabilities: ["images"] } as S),
	).toContain("当前模型未独立验证");
	expect(
		imageSupport({ controller: "ready", capabilities: ["steer"] } as S),
	).toContain("工具读图能力待验证");
	expect(
		imageSupport({ controller: "stopped", capabilities: ["images"] } as S),
	).toContain("未知");
});

it("records a manual failure against a fixed candidate without claiming final acceptance", async () => {
	const commit = "a".repeat(40);
	api.GET.mockResolvedValue({
		data: [
			{
				commit,
				branch: "candidate",
				tasks: ["task"],
				checks: [],
				createdAt: "",
			},
		],
	});
	api.POST.mockResolvedValue({ data: { accepted: true } });
	const c = new QueryClient();
	clients.push(c);
	render(
		<QueryClientProvider client={c}>
			<PeerIntegrationEvidence roomId="r" epoch={4} />
		</QueryClientProvider>,
	);
	await screen.findByText("未记录集成测试");
	fireEvent.change(screen.getByLabelText("测试候选版本"), {
		target: { value: commit },
	});
	for (const [label, value] of [
		["执行人", "reviewer"],
		["测试命令", "pytest"],
		["执行目录", "/fixture"],
		["退出码", "1"],
		["验收范围", "独立重跑失败；浏览器未验证"],
	])
		fireEvent.change(screen.getByLabelText(label), { target: { value } });
	fireEvent.click(screen.getByRole("button", { name: "保存手动测试记录" }));
	await screen.findByText("已保存手动测试声明；不代表平台独立验收通过。");
	expect(api.POST.mock.calls[0][1].body).toMatchObject({
		commit,
		exitCode: 1,
		executor: "reviewer",
		epoch: 4,
		command: "pytest",
		cwd: "/fixture",
	});
});

it("replaces waiting copy with the persisted rejection after the provider resolves approval", async () => {
	const data = records("a");
	data.activities = [
		{
			...data.activities[0],
			activityKind: "approval",
			status: "pending",
			requestId: "request-a",
			detail: {
				decisions: [{ id: "cancel", label: "Cancel", kind: "reject_once" }],
			},
		} as any,
	];
	api.GET.mockImplementation(async () => ({ data: structuredClone(data) }));
	api.POST.mockImplementation(async () => {
		data.state = "interrupted";
		data.activities[0].status = "resolved";
		(data.activities[0].detail as any).decision = "cancel";
		return { data: { status: "decision_submitted" } };
	});
	mount();
	openDetails(screen.getAllByText("执行记录")[0].closest("details")!);
	fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
	await screen.findByText("审批已拒绝");
	await screen.findByText("审批结果已记录。");
	expect(screen.queryByText("审批决定已提交，等待状态更新。")).toBeNull();
	expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
	expect(api.POST).toHaveBeenCalledTimes(1);
});

it("shows confirmed interruption only after the native state changes", async () => {
	const data = records("a");
	api.GET.mockImplementation(async () => ({ data: structuredClone(data) }));
	api.POST.mockImplementation(async () => {
		data.state = "interrupted";
		return { data: { status: "stop_requested" } };
	});
	mount();
	openDetails(screen.getAllByText("执行记录")[0].closest("details")!);
	fireEvent.click(await screen.findByText("停止本次运行"));
	await screen.findByText("客户端已确认本次运行中断。");
	expect(screen.queryByText("停止请求已提交，等待客户端确认停止。")).toBeNull();
	expect(api.POST).toHaveBeenCalledTimes(1);
});

it("loads delivery evidence on demand and distinguishes reported checks", async () => {
	api.GET.mockResolvedValue({ data: [{
		action: "submit", actor: "a", revision: 1, commit: "b".repeat(40),
		evidence: "Fixed login", files: ["src/login.ts"],
		tests: [{ command: "npm test", cwd: "/fixture", exitCode: 0, scope: "login unit tests" }],
	}] });
	const client = new QueryClient();
	clients.push(client);
	render(
		<QueryClientProvider client={client}>
			<PeerTaskEvidence roomId="room" taskId="login" label={() => "GP1"} linkedDelivery={{ id: "da", owner: "a" }} compact />
		</QueryClientProvider>,
	);
	expect(api.GET).not.toHaveBeenCalled();
	openDetails(screen.getByText("查看改动与测试记录").closest("details")!);
	expect(await screen.findByText("声明的变更文件：src/login.ts")).toBeVisible();
	expect(screen.getByText(/成员报告的检查：npm test/)).toHaveTextContent("退出码：0");
	expect(screen.getByText("执行记录")).toBeVisible();
	expect(api.GET).toHaveBeenCalledTimes(1);
});
