import { TooltipProvider } from "./ui/tooltip";
import {
	act,
	fireEvent,
	render,
	screen,
	waitFor,
	cleanup,
	within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { PeerRoom } from "./PeerRoom";
import type { WorkspaceSession } from "../types/workspace";
const api = vi.hoisted(() => ({ GET: vi.fn(), PUT: vi.fn(), POST: vi.fn() }));
vi.mock("../lib/api-client", () => ({ apiClient: api }));
vi.mock("./chat/ChatMarkdown", () => ({
	ChatMarkdown: ({ text }: { text: string }) => <p>{text}</p>,
}));
const sessions = ["a", "b", "c"].map(
	(id) =>
		({
			id,
			title: `成员 ${id}`,
			provider: "codex",
			mode: "chat",
			workspaceId: "p",
		}) as WorkspaceSession,
);
let room: any;
function mount() {
	const client = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	return {
		...render(
			<QueryClientProvider client={client}>
				<TooltipProvider>
					<PeerRoom
						projectId="p"
						sessions={sessions}
						onManageSessions={() => {}}
					/>
				</TooltipProvider>
			</QueryClientProvider>,
		),
		client,
	};
}

beforeEach(() => {
	vi.clearAllMocks();
	sessionStorage.clear();
	room = {
		id: "project-p",
		projectId: "p",
		epoch: 1,
		members: ["a", "b"],
		budget: 24,
		used: 0,
		paused: false,
	};
	api.GET.mockImplementation(async (path: string) =>
		path.endsWith("/agents/readiness") ? {data:{agents:[]}} : path === "/api/v1/settings" ? {data:{chatHarnesses:[]}} : path.endsWith("/tasks")
			? { data: [] }
			: path.endsWith("/peer-binding")
				? { error: { message: "unbound" } }
				: path.endsWith("/history")
					? {
							data: [
								{
									replies: [
										{
											id: "answer",
											text: "准确关联的回复",
											streaming: false,
											turnState: "completed",
										},
									],
									id: "receipt",
									sender: "",
									target: "a",
									message: "本房间的消息",
									state: "uncertain",
									createdAt: "2026-11-11T10:00:00Z",
									epoch: 1,
								},
							],
						}
					: path.includes("peer-rooms")
						? { data: room, response: { status: room ? 200 : 404 } }
						: {
								data: {
									controller: "ready",
									messages: [
										{
											id: "1",
											role: "assistant",
											text: "原生回复",
											createdAt: "2026-11-11T10:00:00Z",
											sequence: 1,
										},
									],
									hasMoreBefore: false,
								},
							},
	);
	api.PUT.mockImplementation(async (_path, { body }) => {
		room = { ...body, epoch: body.epoch + 1 };
		return { data: room };
	});
	api.POST.mockResolvedValue({
		data: {
			deliveries: [
				{ session: "a", state: "accepted" },
				{ session: "b", state: "uncertain" },
			],
		},
	});
});
afterEach(cleanup);
it("broadcasts once to same-provider sessions and blocks replay of uncertain delivery", async () => {
	mount();
	await screen.findAllByText("准确关联的回复");
	fireEvent.change(screen.getByLabelText("群聊消息"), {
		target: { value: "请一起分析" },
	});
	fireEvent.click(screen.getByRole("button", { name: /发送消息/ }));
	await screen.findByText(/部分结果未确认/);
	expect(api.POST).toHaveBeenCalledTimes(1);
	expect(api.POST.mock.calls[0][1].body).toMatchObject({
		epoch: 1,
		message: "请一起分析",
	});
	expect(screen.getByRole("button", { name: /发送消息/ })).toBeDisabled();
	expect(screen.getByLabelText("群聊消息")).toHaveValue("");
	fireEvent.click(screen.getByRole("button", { name: "已核查，撰写新消息" }));
	expect(api.POST).toHaveBeenCalledTimes(1);
});
it("keeps the draft and attachments when every member was blocked", async () => {
	api.POST.mockResolvedValue({
		data: { deliveries: [
			{ id: "blocked-a", session: "a", state: "blocked", detail: "客户端不可用" },
			{ id: "blocked-b", session: "b", state: "blocked", detail: "成员刚被停用" },
		] },
	});
	mount();
	await screen.findByText("本房间的消息");
	const input = screen.getByLabelText("群聊消息");
	fireEvent.change(input, { target: { value: "待发送的说明" } });
	fireEvent.paste(input, {
		clipboardData: { files: [new File(["proof"], "blocked.txt", { type: "text/plain" })] },
	});
	const attachment = await screen.findByRole("button", { name: "移除附件 blocked.txt" });
	fireEvent.click(screen.getByRole("button", { name: "发送消息" }));

	expect(await screen.findByText("没有成员收到消息，草稿已保留。")).toBeVisible();
	expect(input).toHaveValue("待发送的说明");
	expect(attachment).toBeVisible();
	expect(screen.getByRole("button", { name: "发送消息" })).toBeEnabled();
	expect(screen.queryByText(/部分结果未确认/)).not.toBeInTheDocument();
	expect(api.POST).toHaveBeenCalledTimes(1);
	fireEvent.click(attachment);
});

it("preserves a draft on return and keeps a pre-delivery room conflict editable", async () => {
	api.POST.mockResolvedValue({
		error: { code: "PEER_BROADCAST_REJECTED", message: "房间已更新" },
		response: { status: 409 },
	});
	const first = mount();
	await screen.findByText("本房间的消息");
	fireEvent.change(screen.getByLabelText("群聊消息"), {
		target: { value: "切换页面后继续编辑" },
	});
	first.unmount();
	mount();
	const input = screen.getByLabelText("群聊消息");
	expect(input).toHaveValue("切换页面后继续编辑");
	await screen.findByText("本房间的消息");
	fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
	expect(await screen.findByText("房间已更新")).toBeVisible();
	expect(input).toHaveValue("切换页面后继续编辑");
	expect(input).toBeEnabled();
	expect(screen.queryByText(/部分结果未确认/)).not.toBeInTheDocument();
	expect(api.POST).toHaveBeenCalledTimes(1);
});

it("removes a stale member whose session has disappeared", async () => {
	room.members = ["a", "missing"];
	mount();
	await screen.findByText("本房间的消息");
	await userEvent.click(screen.getByRole("button", { name: "群聊菜单" }));
	await userEvent.click(await screen.findByRole("menuitem", { name: "房间设置" }));
	const settings = screen.getByRole("dialog", { name: "群聊设置" });
	await userEvent.click(within(settings).getByText("已加入房间：2 位"));
	const stale = within(settings).getByRole("checkbox", { name: /会话 missing.*会话不可用/ });
	expect(stale).toBeChecked();
	await userEvent.click(stale);
	await userEvent.click(within(settings).getByRole("button", { name: "保存选择（1 位）" }));

	await waitFor(() => expect(room.members).toEqual(["a"]));
	expect(api.PUT).toHaveBeenCalledTimes(1);
	expect(api.POST).not.toHaveBeenCalled();
});

it("creates a room with selected session IDs and restores server history on remount", async () => {
	room = null;
	const first = mount();
	await userEvent.click(screen.getByRole("button", { name: "群聊菜单" }));
	await userEvent.click(
		await screen.findByRole("menuitem", { name: "房间设置" }),
	);
	await screen.findByText("创建项目房间");
	fireEvent.click(screen.getAllByRole("checkbox")[0]);
	fireEvent.click(screen.getAllByRole("checkbox")[1]);
	fireEvent.click(screen.getByRole("button", { name: "创建项目房间" }));
	await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1));
	expect(api.PUT.mock.calls[0][1].body.members).toEqual(["a", "b"]);
	first.unmount();
	mount();
	expect((await screen.findAllByText("准确关联的回复")).length).toBe(1);
});
it("pauses without altering members and disables sending", async () => {
	mount();
	await screen.findAllByText("准确关联的回复");
	await userEvent.click(screen.getByRole("button", { name: "群聊菜单" }));
	await userEvent.click(
		await screen.findByRole("menuitem", { name: "暂停房间" }),
	);
	await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1));
	expect(api.PUT.mock.calls[0][1].body).toMatchObject({
		paused: true,
		members: ["a", "b"],
		epoch: 1,
	});
	fireEvent.change(screen.getByLabelText("群聊消息"), {
		target: { value: "不应该发送" },
	});
	expect(screen.getByRole("button", { name: /发送消息/ })).toBeDisabled();
});
it("does not turn a failed room lookup into a new room", async () => {
	api.GET.mockResolvedValue({
		error: { message: "offline" },
		response: { status: 503 },
	});
	mount();
	await screen.findByRole("alert");
	await userEvent.click(screen.getByRole("button", { name: "群聊菜单" }));
	await userEvent.click(
		await screen.findByRole("menuitem", { name: "房间设置" }),
	);
	expect(screen.getByRole("button", { name: "创建项目房间" })).toBeDisabled();
	expect(api.PUT).not.toHaveBeenCalled();
});

it("retains an interrupted-send warning across remount without replay", async () => {
	sessionStorage.setItem("peer-room-pending:project-p", "1");
	mount();
	await screen.findByText(/部分结果未确认/);
	expect(screen.getByLabelText("群聊消息")).toBeDisabled();
	expect(api.POST).not.toHaveBeenCalled();
});
it("allows a new message after the legacy lifetime budget is exhausted", async () => {
	room.used = room.budget;
	mount();
	await screen.findAllByText("准确关联的回复");
	fireEvent.change(screen.getByLabelText("群聊消息"), {
		target: { value: "new round" },
	});
	expect(screen.getByRole("button", { name: /发送消息/ })).not.toBeDisabled();
	fireEvent.click(screen.getByRole("button", { name: /发送消息/ }));
	await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
});

it("keeps durable receipts after remount and labels native history separately", async () => {
	const first = mount();
	await screen.findByText("本房间的消息");
	expect(screen.queryByText(/查看成员原生会话/)).not.toBeInTheDocument();
	expect(screen.queryByText("原生回复")).not.toBeInTheDocument();
	first.unmount();
	mount();
	await screen.findByText("本房间的消息");
	expect(
		screen.getByText(/本轮已回复（投递回执仍待核查）/),
	).toBeInTheDocument();
	expect(api.POST).not.toHaveBeenCalled();
});

it("renders exact correlated replies in the room and grants tools only on explicit click", async () => {
	mount();
	await screen.findByText("准确关联的回复");
	expect(api.POST).not.toHaveBeenCalled();
	await userEvent.click(screen.getByRole("button", { name: "群聊菜单" }));
	await userEvent.click(
		await screen.findByRole("menuitem", { name: "房间设置" }),
	);
	fireEvent.click(screen.getByRole("button", { name: "连接 / 更新同伴工具" }));
	await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
	expect(api.POST.mock.calls[0][0]).toContain("/bind");
	expect(api.POST.mock.calls[0][1].body).toEqual({ epoch: 1 });
});

it.each([false, true])("adds a native member without a message or implicit grant (first member: %s)", async (firstMember) => {
	if (firstMember) room = null;
	const get = api.GET.getMockImplementation()!;
	api.GET.mockImplementation((path: string, ...args: any[]) =>
		path.endsWith("/agents/readiness")
			? Promise.resolve({
					data: {
						agents: [
							{
								id: "codex", label:"Codex",
								installation: { state: "installed" },
								effectiveReadiness: "ready",
							},
						],
					},
				})
			: path === "/api/v1/settings" ? Promise.resolve({data:{chatHarnesses:["codex"]}}) : get(path, ...args),
	);
	let n = 0;
	api.POST.mockImplementation(async () => ({
		data: { session: { id: `new-${++n}` } },
	}));
	mount();
	await waitFor(() => expect(api.GET).toHaveBeenCalledWith(
		"/api/v1/peer-rooms/{roomId}", expect.anything(),
	));
	await userEvent.click(screen.getByRole("button", { name: "添加" }));
	await userEvent.click(
		await screen.findByRole("menuitem", { name: /Add agent/ }),
	);
	await waitFor(() =>
		expect(screen.getByRole("button", {name:"添加 Codex"})).toBeEnabled(),
	);
	fireEvent.click(screen.getByRole("button", {name:"添加 Codex"}));
	await waitFor(() => expect(api.PUT).toHaveBeenCalled());
	expect(room.members).toEqual(firstMember ? ["new-1"] : ["a", "b", "new-1"]);
	expect(api.POST.mock.calls.every((c) => c[0] === "/api/v1/sessions")).toBe(
		true,
	);
	expect(api.POST).toHaveBeenCalledTimes(1);
	await waitFor(() =>
		expect(
			screen.queryByRole("dialog", { name: "添加" }),
		).not.toBeInTheDocument(),
	);
});

it("distinguishes saved members from draft selections and saves explicitly", async () => {
	mount();
	await screen.findByText("本房间的消息");
	await userEvent.click(screen.getByRole("button", { name: "群聊菜单" }));
	await userEvent.click(
		await screen.findByRole("menuitem", { name: "房间设置" }),
	);
	fireEvent.click(screen.getByText(/已加入房间：/));
	fireEvent.click(screen.getAllByRole("checkbox")[2]);
	expect(
		screen.getByText(/有未保存修改：已加入 2 位，当前选择 3 位/),
	).toBeInTheDocument();
	expect(screen.getByText("待加入 · 未保存")).toBeInTheDocument();
	expect(api.PUT).not.toHaveBeenCalled();
	const reload = new Event("beforeunload", { cancelable: true });
	window.dispatchEvent(reload);
	expect(reload.defaultPrevented).toBe(true);
	expect(
		screen.getByRole("button", { name: "连接 / 更新同伴工具" }),
	).toBeDisabled();
	fireEvent.click(screen.getByRole("button", { name: "保存选择（3 位）" }));
	await screen.findByText(/已保存 3 位成员/);
	expect(room.members).toEqual(["a", "b", "c"]);
	expect(screen.queryByText("待加入 · 未保存")).not.toBeInTheDocument();
	expect(screen.getByRole("button", { name: "成员已保存" })).toBeDisabled();
});
it("keeps failed-save selections and detects a changed room before overwriting it", async () => {
	const view = mount();
	await screen.findByText("本房间的消息");
	await userEvent.click(screen.getByRole("button", { name: "群聊菜单" }));
	await userEvent.click(
		await screen.findByRole("menuitem", { name: "房间设置" }),
	);
	fireEvent.click(screen.getByText(/已加入房间：/));
	fireEvent.click(screen.getAllByRole("checkbox")[2]);
	api.PUT.mockResolvedValueOnce({ error: { message: "保存失败" } });
	fireEvent.click(screen.getByRole("button", { name: "保存选择（3 位）" }));
	await screen.findByRole("alert");
	expect(screen.getAllByRole("checkbox")[2]).toBeChecked();
	room = { ...room, epoch: 2 };
	await act(async () => {
		await view.client.invalidateQueries({
			queryKey: ["peer-room", "project-p"],
		});
	});
	await screen.findByText(/房间已被其他操作更新/);
	expect(
		screen.getByRole("button", { name: "保存选择（3 位）" }),
	).toBeDisabled();
	fireEvent.click(
		screen.getByRole("button", { name: "取消修改，载入已保存成员" }),
	);
	expect(screen.getAllByRole("checkbox")[2]).not.toBeChecked();
	expect(api.PUT).toHaveBeenCalledTimes(1);
});

it("includes enabled stopped members for server-side recovery before delivery", async () => {
	const original = api.GET.getMockImplementation()!;
	api.GET.mockImplementation(async (path, options) =>
		path.endsWith("/conversation") && options.params.path.sessionId === "b"
			? { data: { controller: "stopped", messages: [] } }
			: original(path, options),
	);
	mount();
	await screen.findAllByText("准确关联的回复");
	fireEvent.change(screen.getByLabelText("群聊消息"), {
		target: { value: "do not send" },
	});
	expect(
		screen.getByRole("button", { name: "设置成员 成员 b" }),
	).toHaveAttribute("title", expect.stringContaining("自动连接"));
	fireEvent.click(screen.getByRole("button", { name: /发送消息/ }));
	await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
	expect(api.POST.mock.calls[0][1].body).toMatchObject({
		targets: ["a", "b"],
		connectPeers: true,
	});
});
it("refreshes an unavailable member without dispatching and connects it on the next explicit send", async () => {
	room.members = ["b"];
	let unavailable = true;
	const original = api.GET.getMockImplementation()!;
	api.GET.mockImplementation(async (path, options) =>
		path.endsWith("/conversation") && options.params.path.sessionId === "b"
			? unavailable
				? { error: { message: "状态暂不可读" } }
				: { data: { controller: "stopped", messages: [] } }
			: original(path, options),
	);
	api.POST.mockResolvedValue({
		data: { deliveries: [{ id: "first", session: "b", state: "accepted" }] },
	});
	mount();
	await screen.findByText("本房间的消息");
	const input = screen.getByLabelText("群聊消息");
	fireEvent.change(input, { target: { value: "新任务" } });
	const member = screen.getByRole("button", { name: "设置成员 成员 b" });
	await waitFor(() =>
		expect(member).toHaveAttribute("title", expect.stringContaining("状态读取失败")),
	);
	expect(screen.getByRole("button", { name: "发送消息" })).toBeDisabled();

	await userEvent.click(member);
	unavailable = false;
	await userEvent.click(await screen.findByRole("button", { name: "刷新状态" }));
	await waitFor(() =>
		expect(member).toHaveAttribute("title", expect.stringContaining("自动连接")),
	);
	expect(api.POST).not.toHaveBeenCalled();
	expect(input).toHaveValue("新任务");
	await userEvent.keyboard("{Escape}");
	await userEvent.click(screen.getByRole("button", { name: "发送消息" }));
	await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
	expect(api.POST.mock.calls[0][1].body).toMatchObject({
		targets: ["b"],
		message: "新任务",
		connectPeers: true,
	});
});
it("updates receipts using the exact delivery id rather than another turn from the same member", async () => {
	let state = "running";
	const original = api.GET.getMockImplementation()!;
	api.GET.mockImplementation(async (path, options) =>
		path.endsWith("/history")
			? {
					data: [
						{
							id: "current",
							target: "a",
							state: "accepted",
							turnState: state,
							replies: [],
							message: "current",
						},
						{
							id: "other",
							target: "a",
							state: "accepted",
							turnState: "completed",
							replies: [],
							message: "other",
						},
					],
				}
			: original(path, options),
	);
	api.POST.mockResolvedValue({
		data: { deliveries: [{ id: "current", session: "a", state: "accepted" }] },
	});
	const { client } = mount();
	await screen.findByText("current");
	fireEvent.change(screen.getByLabelText("群聊消息"), {
		target: { value: "new message" },
	});
	fireEvent.click(screen.getByRole("button", { name: /发送消息/ }));
	await screen.findByText("正在工作");
	state = "failed";
	await act(async () => {
		await client.invalidateQueries({
			queryKey: ["peer-room-history", "project-p"],
		});
	});
	await screen.findByText("本轮失败");
	expect(api.POST).toHaveBeenCalledTimes(1);
});

it("sends on Enter but preserves Shift+Enter and IME composition", async () => {
	mount();
	await screen.findAllByText("准确关联的回复");
	const input = screen.getByLabelText("群聊消息");
	fireEvent.change(input, { target: { value: "键盘发送" } });
	for (const options of [
		{ shiftKey: true },
		{ isComposing: true },
		{ keyCode: 229 },
		{ repeat: true },
	]) {
		fireEvent.keyDown(input, { key: "Enter", ...options });
	}
	expect(api.POST).not.toHaveBeenCalled();
	fireEvent.keyDown(input, { key: "Enter" });
	await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
	expect(api.POST.mock.calls[0][1].body.message).toBe("键盘发送");
	fireEvent.keyDown(input, { key: "Enter" });
	expect(api.POST).toHaveBeenCalledTimes(1);
});
it("does not bypass send guards with Enter", async () => {
	mount();
	await screen.findAllByText("准确关联的回复");
	const input = screen.getByLabelText("群聊消息");
	fireEvent.keyDown(input, { key: "Enter" });
	fireEvent.change(input, { target: { value: "   " } });
	fireEvent.keyDown(input, { key: "Enter" });
	expect(api.POST).not.toHaveBeenCalled();
});

it("keeps room administration out of the composer and opens the working attachment picker", async () => {
	mount();
	await screen.findByText("本房间的消息");
	expect(
		screen.queryByRole("button", { name: "房间设置" }),
	).not.toBeInTheDocument();
	expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	await userEvent.click(screen.getByRole("button", { name: "添加" }));
	expect(
		await screen.findByRole("menuitem", { name: /文件/ }),
	).not.toHaveAttribute("aria-disabled", "true");
	expect(
		screen.getByRole("menuitem", { name: /Add agent/ }),
	).toBeInTheDocument();
	expect(api.POST).not.toHaveBeenCalled();
});

it("sends files and images together without requiring text and clears the sent draft", async () => {
	api.POST.mockResolvedValue({
		data: {
			deliveries: [
				{ session: "a", state: "accepted" },
				{ session: "b", state: "accepted" },
			],
		},
	});
	mount();
	await screen.findByText("本房间的消息");
	fireEvent.change(screen.getByLabelText("添加文件或图片"), {
		target: {
			files: [
				new File(["proof"], "说明.txt", { type: "text/plain" }),
				new File(["png"], "图.png", { type: "image/png" }),
			],
		},
	});
	await screen.findByRole("img", { name: "图.png" });
	expect(screen.getByText("图片随消息作为文件发送，由接收成员使用工具读取。")).toBeVisible();
	await waitFor(() =>
		expect(screen.getByRole("button", { name: /发送消息/ })).toBeEnabled(),
	);
	fireEvent.click(screen.getByRole("button", { name: /发送消息/ }));
	await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
	expect(api.POST.mock.calls[0][1].body).toMatchObject({
		message: "",
		targets: ["a", "b"],
		attachments: [
			{ name: "说明.txt", mimeType: "text/plain", data: "cHJvb2Y=" },
			{ name: "图.png", mimeType: "image/png", data: "cG5n" },
		],
	});
	await waitFor(() =>
		expect(screen.queryByLabelText("待发送附件")).not.toBeInTheDocument(),
	);
});
it("keeps rejected attachments editable without replay and supports removal", async () => {
	api.POST.mockResolvedValue({
		error: { message: "unsupported attachment" },
		response: { status: 400 },
	});
	mount();
	await screen.findAllByText("准确关联的回复");
	fireEvent.paste(screen.getByLabelText("群聊消息"), {
		clipboardData: {
			files: [new File(["proof"], "paste.txt", { type: "text/plain" })],
		},
	});
	await screen.findByRole("button", { name: "移除附件 paste.txt" });
	await waitFor(() =>
		expect(screen.getByRole("button", { name: /发送消息/ })).toBeEnabled(),
	);
	fireEvent.click(screen.getByRole("button", { name: /发送消息/ }));
	await screen.findByText("unsupported attachment");
	expect(screen.getByRole("button", { name: /发送消息/ })).toBeEnabled();
	expect(api.POST).toHaveBeenCalledTimes(1);
	fireEvent.click(screen.getByRole("button", { name: "移除附件 paste.txt" }));
	expect(screen.getByRole("button", { name: /发送消息/ })).toBeDisabled();
	expect(api.POST).toHaveBeenCalledTimes(1);
});

it("excludes disabled members from user broadcast", async () => {
	room.memberSettings = { b: { disabled: true } };
	mount();
	await screen.findByLabelText("暂不可用");
	fireEvent.change(screen.getByLabelText("群聊消息"), {
		target: { value: "new" },
	});
	fireEvent.click(screen.getByRole("button", { name: /发送消息/ }));
	await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
	expect(api.POST.mock.calls[0][1].body.targets).toEqual(["a"]);
});

it("sends long Unicode requirements intact and bounds oversized drafts", async () => {
	mount();
	await screen.findAllByText("准确关联的回复");
	const input = screen.getByLabelText("群聊消息");
	fireEvent.change(input, { target: { value: "x".repeat(256 * 1024 + 1) } });
	expect(screen.getByRole("button", { name: /发送消息/ })).toBeDisabled();
	expect(screen.getByText(/Messages are limited to 256 KB/)).toBeTruthy();
	const text = "需求与代码\n".repeat(2000);
	fireEvent.change(input, { target: { value: text } });
	fireEvent.keyDown(input, { key: "Enter" });
	await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
	expect(api.POST.mock.calls[0][1].body.message).toBe(text);
});

it("uses only the left add menu with inline agent navigation", async () => {
 mount(); await screen.findByText("本房间的消息");
 expect(screen.queryByRole("button",{name:"添加成员"})).not.toBeInTheDocument();
 await userEvent.click(screen.getByRole("button",{name:"添加"}));
 const panel=await screen.findByRole("dialog",{name:"添加"});
 expect(panel).toHaveAttribute("data-align","start");
 expect(screen.getByRole("menuitem",{name:"添加图片"})).toBeInTheDocument();
 await userEvent.click(screen.getByRole("menuitem",{name:"Add agent"}));
 await userEvent.click(await screen.findByRole("button",{name:"返回添加菜单"}));
 expect(screen.getByRole("menuitem",{name:"添加文件"})).toBeInTheDocument();
 expect(api.POST).not.toHaveBeenCalled();
});

it("removes only the chosen member without sending or deleting a session", async () => {
 room.members = ["a", "b", "c"];
 room.memberSettings = {a: {disabled: false}, c: {disabled: true}};
 mount();
 await userEvent.click(await screen.findByRole("button", {name: "设置成员 成员 c"}));
 await userEvent.click(screen.getByRole("button", {name: "移出群聊"}));
 await waitFor(() => expect(room.members).toEqual(["a", "b"]));
 expect(room.memberSettings).toEqual({a: {disabled: false}});
 expect(api.PUT).toHaveBeenCalledTimes(1);
 expect(api.POST).not.toHaveBeenCalled();
 expect(screen.queryByRole("button", {name: "设置成员 成员 c"})).not.toBeInTheDocument();
});
it("keeps the member and exposes a failed removal without retry", async () => {
 room.members = ["a", "b", "c"];
 api.PUT.mockResolvedValue({error: {message: "房间已更新，请重试"}});
 mount();
 await userEvent.click(await screen.findByRole("button", {name: "设置成员 成员 c"}));
 await userEvent.click(screen.getByRole("button", {name: "移出群聊"}));
 await screen.findByText("房间已更新，请重试");
 expect(room.members).toEqual(["a", "b", "c"]);
 expect(api.PUT).toHaveBeenCalledTimes(1);
 expect(api.POST).not.toHaveBeenCalled();
});
it("explains the one-member minimum at the removal entry", async () => {
 room.members = ["a"];
 mount();
 await userEvent.click(await screen.findByRole("button", {name: "设置成员 成员 a"}));
 expect(screen.getByRole("button", {name: "移出群聊"})).toBeDisabled();
 expect(screen.getByRole("button", {name: "移出群聊"})).toHaveAttribute("title", "群聊至少保留一位成员；可使用开关暂时停用");
 expect(api.PUT).not.toHaveBeenCalled();
});

it("removes to one member, sends to that member, and rejoins the same session", async () => {
	mount();
	await userEvent.click(await screen.findByRole("button", { name: "设置成员 成员 b" }));
	await userEvent.click(screen.getByRole("button", { name: "移出群聊" }));
	await waitFor(() => expect(room.members).toEqual(["a"]));

	api.POST.mockResolvedValue({ data: { deliveries: [{ session: "a", state: "accepted" }] } });
	fireEvent.change(screen.getByLabelText("群聊消息"), { target: { value: "单人开始" } });
	await userEvent.click(screen.getByRole("button", { name: "发送消息" }));
	await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
	expect(api.POST.mock.calls[0][0]).toContain("/broadcast");
	expect(api.POST.mock.calls[0][1].body).toMatchObject({ targets: ["a"], message: "单人开始" });
	await waitFor(() => expect(screen.getByRole("button", { name: "添加" })).toBeEnabled());

	await userEvent.click(screen.getByRole("button", { name: "添加" }));
	await userEvent.click(await screen.findByRole("menuitem", { name: "Add agent" }));
	await userEvent.click(await screen.findByRole("button", { name: "加入已有会话 成员 b" }));
	await waitFor(() => expect(room.members).toEqual(["a", "b"]));
	expect(api.POST).toHaveBeenCalledTimes(1);
});


it("shows only this member's latest observed delivery problem and distinguishes turn failure", async () => {
	const original = api.GET.getMockImplementation()!;
	const d = (id: string, target: string, state: string, turnState?: string) => ({
		id,
		target,
		state,
		turnState,
		message: id,
		replies: [],
		createdAt: "2026-11-11T10:00:00Z",
	});
	api.GET.mockImplementation(async (path, options) =>
		path.endsWith("/history")
			? { data: [
				{ ...d("old", "a", "blocked"), detail: "客户端尚未登录" },
				d("new", "a", "accepted", "failed"),
				{ ...d("peer", "b", "uncertain"), detail: "等待客户端响应超时" },
			] }
			: original(path, options),
	);
	mount();
	const a = await screen.findByRole("button", { name: "设置成员 成员 a" });
	await waitFor(() => expect(a).toHaveAttribute("title", expect.stringContaining("已送达，处理失败")));
	const b = screen.getByRole("button", { name: "设置成员 成员 b" });
	expect(b).toHaveAttribute("title", expect.stringContaining("送达未确认"));
	await userEvent.click(b);
	expect(screen.getByText("上次送达未确认")).toBeInTheDocument();
	expect(screen.getByText(/等待客户端响应超时/, { selector: "p.break-words" })).toBeInTheDocument();
	expect(api.POST).not.toHaveBeenCalled();
});
