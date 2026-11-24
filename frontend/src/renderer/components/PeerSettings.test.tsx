import userEvent from "@testing-library/user-event";
import {
	render,
	screen,
	fireEvent,
	waitFor,
	cleanup,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi, beforeEach, afterEach, it, expect } from "vitest";
import { PeerAccessControl } from "./PeerAccessControl";
import { PeerAgentSettings } from "./PeerAgentSettings";
import type { WorkspaceSession } from "../types/workspace";
const api = vi.hoisted(() => ({ GET: vi.fn(), PATCH: vi.fn(), POST: vi.fn() }));
vi.mock("../lib/api-client", () => ({ apiClient: api }));
const session = {
	id: "a",
	title: "开发",
	provider: "codex",
	mode: "chat",
	workspaceId: "p",
} as WorkspaceSession;
function mount(ui: React.ReactNode) {
	return render(
		<QueryClientProvider
			client={
				new QueryClient({ defaultOptions: { queries: { retry: false } } })
			}
		>
			{ui}
		</QueryClientProvider>,
	);
}
beforeEach(() => {
	vi.clearAllMocks();
	api.PATCH.mockResolvedValue({ data: {} });
	api.POST.mockResolvedValue({ data: {} });
	api.GET.mockImplementation(async (path: string) =>
		path.endsWith("permission-modes")
			? {
					data: {
						agents: [
							{
								agent: "codex",
								modes: [
									"default",
									"accept-edits",
									"auto",
									"bypass-permissions",
								],
							},
						],
					},
				}
			: {
					data: {
						settings: {
							approvalMode: "auto",
							model: "native-model",
							reasoningEffort: "high",
						},
					},
				},
	);
});
afterEach(cleanup);
it("reads permissions without a grant and applies only the explicitly selected member setting", async () => {
	mount(
		<PeerAccessControl
			sessions={[session]}
			disabled={false}
			onBusyChange={() => {}}
		/>,
	);
	fireEvent.click(screen.getByRole("button", { name: "访问权限" }));
	expect(
		await screen.findByRole("radio", { name: /帮我批准/ }),
	).toHaveAttribute("aria-checked", "true");
	expect(api.PATCH).not.toHaveBeenCalled();
	fireEvent.click(screen.getByRole("radio", { name: /请求批准/ }));
	expect(api.PATCH).not.toHaveBeenCalled();
	fireEvent.click(screen.getByRole("button", { name: "应用权限" }));
	await waitFor(() => expect(api.PATCH).toHaveBeenCalledTimes(1));
	expect(api.PATCH.mock.calls[0][1]).toMatchObject({
		params: { path: { sessionId: "a" } },
		body: {
			approvalMode: "accept-edits",
			model: "native-model",
			reasoningEffort: "high",
		},
	});
});
it("reports a denied permission change without hiding failure or retrying", async () => {
	api.PATCH.mockResolvedValue({ error: { message: "原生客户端拒绝修改" } });
	mount(
		<PeerAccessControl
			sessions={[session]}
			disabled={false}
			onBusyChange={() => {}}
		/>,
	);
	fireEvent.click(screen.getByRole("button", { name: "访问权限" }));
	fireEvent.click(await screen.findByRole("radio", { name: /完全访问权限/ }));
	fireEvent.click(screen.getByRole("button", { name: "应用权限" }));
	expect(await screen.findByRole("alert")).toHaveTextContent(
		"原生客户端拒绝修改",
	);
	expect(api.PATCH).toHaveBeenCalledTimes(1);
});
it("does not invent permission choices when the native client has none", async () => {
	api.GET.mockResolvedValue({ data: { agents: [], settings: {} } });
	mount(
		<PeerAccessControl
			sessions={[session]}
			disabled={false}
			onBusyChange={() => {}}
		/>,
	);
	fireEvent.click(screen.getByRole("button", { name: "访问权限" }));
	await screen.findByText("此客户端未提供可切换的权限模式。");
	expect(screen.queryByRole("radio")).not.toBeInTheDocument();
	expect(api.PATCH).not.toHaveBeenCalled();
});
it("switches Codex models through the native menu and preserves permissions", async () => {
	api.GET.mockImplementation(async (path: string) =>
		path.endsWith("models")
			? {
					data: {
						models: [
							{
								id: "one",
								displayName: "模型一",
								default: true,
								efforts: ["high"],
								defaultEffort: "high",
							},
							{
								id: "two",
								displayName: "模型二",
								efforts: ["low"],
								defaultEffort: "low",
							},
						],
						selected: {
							model: "one",
							approvalMode: "auto",
							reasoningEffort: "high",
						},
					},
				}
			: { data: { options: [] } },
	);
	mount(
		<PeerAgentSettings
			session={session}
			issue=""
			disabled={false}
			onBusyChange={() => {}}
			onRenamed={() => {}}
		/>,
	);
	await userEvent.click(screen.getByRole("button", { name: "设置成员 开发" }));
	const user = userEvent.setup();
	(await screen.findByRole("menuitem", { name: /Model/ })).focus();
	await user.keyboard("{ArrowRight}");
	await user.click(
		await screen.findByRole("menuitemradio", { name: "模型二" }),
	);
	await waitFor(() => expect(api.PATCH).toHaveBeenCalledTimes(1));
	expect(api.PATCH.mock.calls[0][1].body).toEqual({
		model: "two",
		reasoningEffort: undefined,
		approvalMode: "auto",
	});
});
it("uses ACP option identifiers and opaque values, and persists the display name", async () => {
	api.GET.mockImplementation(async (path: string) =>
		path.endsWith("models")
			? { data: { models: [] } }
			: {
					data: {
						options: [
							{
								id: "model",
								name: "Model",
								type: "select",
								category: "model",
								currentValue: '["p","a"]',
								choices: [
									{ value: '["p","a"]', name: "原模型" },
									{ value: '["p","b"]', name: "新模型" },
								],
							},
						],
					},
				},
	);
	const renamed = vi.fn();
	mount(
		<PeerAgentSettings
			session={{ ...session, provider: "deepseek-harness" }}
			issue=""
			disabled={false}
			onBusyChange={() => {}}
			onRenamed={renamed}
		/>,
	);
	await userEvent.click(screen.getByRole("button", { name: "设置成员 开发" }));
	const user = userEvent.setup();
	(await screen.findByRole("menuitem", { name: /Model/ })).focus();
	await user.keyboard("{ArrowRight}");
	await user.click(
		await screen.findByRole("menuitemradio", { name: "新模型" }),
	);
	await waitFor(() => expect(api.PATCH).toHaveBeenCalledTimes(1));
	expect(api.PATCH.mock.calls[0]).toEqual([
		"/api/v1/sessions/{sessionId}/conversation/config-options/{configId}",
		{
			params: { path: { sessionId: "a", configId: "model" } },
			body: { value: '["p","b"]' },
		},
	]);
	if (!screen.queryByLabelText("成员名称"))
		await user.click(screen.getByRole("button", { name: "设置成员 开发" }));
	await waitFor(() => expect(screen.getByLabelText("成员名称")).toBeEnabled());
	fireEvent.change(screen.getByLabelText("成员名称"), {
		target: { value: "验证伙伴" },
	});
	fireEvent.keyDown(screen.getByLabelText("成员名称"), { key: "Enter" });
	fireEvent.blur(screen.getByLabelText("成员名称"));
	await waitFor(() => expect(renamed).toHaveBeenCalledWith("验证伙伴"));
	expect(api.PATCH.mock.calls[1]).toEqual([
		"/api/v1/sessions/{sessionId}",
		{ params: { path: { sessionId: "a" } }, body: { displayName: "验证伙伴" } },
	]);
});
it("keeps failed inline renames visible without a save button or automatic replay", async () => {
	api.PATCH.mockResolvedValue({ error: { message: "名称保存失败" } });
	mount(
		<PeerAgentSettings
			session={session}
			issue="已停止"
			disabled={false}
			onBusyChange={() => {}}
			onRenamed={() => {}}
		/>,
	);
	await userEvent.click(screen.getByRole("button", { name: "设置成员 开发" }));
	const input = screen.getByLabelText("成员名称");
	fireEvent.change(input, { target: { value: "测试名称" } });
	fireEvent.blur(input);
	expect(await screen.findByRole("alert")).toHaveTextContent("名称保存失败");
	expect(input).toHaveValue("测试名称");
	expect(screen.queryByText("保存名称")).not.toBeInTheDocument();
	expect(api.PATCH).toHaveBeenCalledTimes(1);
});
it("uses per-session context readings, clamps overflow and distinguishes unknown from empty", () => {
	const props = {
		session,
		issue: "",
		disabled: false,
		onBusyChange: () => {},
		onRenamed: () => {},
	};
	const view = mount(
		<PeerAgentSettings
			{...props}
			usage={{ contextUsed: 25, contextWindow: 100 }}
		/>,
	);
	expect(
		screen.getByRole("img", { name: /上下文已用 25%/ }),
	).toBeInTheDocument();
	view.unmount();
	mount(
		<PeerAgentSettings
			{...props}
			usage={{ contextUsed: 120, contextWindow: 100 }}
		/>,
	);
	expect(
		screen.getByRole("img", { name: /上下文已用 100%/ }),
	).toBeInTheDocument();
	cleanup();
	mount(
		<PeerAgentSettings
			{...props}
			usage={{ contextUsed: 20, contextWindow: 0 }}
		/>,
	);
	expect(
		screen.getByRole("img", { name: "上下文用量未上报" }),
	).toBeInTheDocument();
	cleanup();
	mount(
		<PeerAgentSettings
			{...props}
			usage={{ contextUsed: 0, contextWindow: 100 }}
		/>,
	);
	expect(
		screen.getByRole("img", { name: /上下文已用 0%/ }),
	).toBeInTheDocument();
});

it("defaults to enabled and unlimited, and saves the member switch without a save button", async () => {
	const change = vi.fn().mockResolvedValue(undefined);
	mount(
		<PeerAgentSettings
			session={session}
			issue=""
			disabled={false}
			onBusyChange={() => {}}
			onRenamed={() => {}}
			onMemberSettingsChange={change}
		/>,
	);
	await userEvent.click(screen.getByRole("button", { name: "设置成员 开发" }));
	expect(screen.getByRole("switch", { name: "启用成员" })).toBeChecked();
	expect(screen.getByLabelText("主动联系同伴")).toHaveValue("unlimited");
	fireEvent.click(screen.getByRole("switch", { name: "启用成员" }));
	await waitFor(() => expect(change).toHaveBeenCalledWith({ disabled: true }));
});

it("saves a per-round limit and can close proactive contact", async () => {
	const change = vi.fn().mockResolvedValue(undefined);
	mount(
		<PeerAgentSettings
			session={session}
			issue=""
			disabled={false}
			onBusyChange={() => {}}
			onRenamed={() => {}}
			memberSettings={{ disabled: false, peerLimit: 8 }}
			onMemberSettingsChange={change}
		/>,
	);
	await userEvent.click(screen.getByRole("button", { name: "设置成员 开发" }));
	fireEvent.change(screen.getByLabelText("每轮主动联系次数"), {
		target: { value: "3" },
	});
	fireEvent.blur(screen.getByLabelText("每轮主动联系次数"));
	await waitFor(() =>
		expect(change).toHaveBeenCalledWith({ disabled: false, peerLimit: 3 }),
	);
	await waitFor(() =>
		expect(screen.getByLabelText("主动联系同伴")).not.toBeDisabled(),
	);
	fireEvent.change(screen.getByLabelText("主动联系同伴"), {
		target: { value: "off" },
	});
	await waitFor(() =>
		expect(change).toHaveBeenCalledWith({ disabled: false, peerLimit: 0 }),
	);
});

it("keeps the enable switch beside the native-session action in the name header", async () => {
	mount(
		<PeerAgentSettings
			session={session}
			issue=""
			disabled={false}
			onBusyChange={() => {}}
			onRenamed={() => {}}
			onOpenSession={() => {}}
			onMemberSettingsChange={vi.fn().mockResolvedValue(undefined)}
		/>,
	);
	await userEvent.click(screen.getByRole("button", { name: "设置成员 开发" }));
	const toggle = screen.getByRole("switch", { name: "启用成员" });
	expect(toggle.parentElement).toContainElement(
		screen.getByRole("button", { name: "打开原生会话" }),
	);
	expect(toggle.parentElement).toContainElement(
		screen.getByLabelText("成员名称"),
	);
	expect(screen.queryByText("启用")).not.toBeInTheDocument();
});

it("reads connection status without resuming or sending a task", async () => {
	const refresh = vi.fn().mockResolvedValue(undefined);
	api.GET.mockResolvedValue({ error: { code: "CHAT_CONTROLLER_NOT_READY" } });
	mount(
		<PeerAgentSettings
			session={session}
			issue=""
			disabled={false}
			onBusyChange={() => {}}
			onRenamed={() => {}}
			onRefreshStatus={refresh}
		/>,
	);
	await userEvent.click(screen.getByRole("button", { name: "设置成员 开发" }));
	expect(await screen.findByText(/客户端尚未连接/)).toBeInTheDocument();
	await userEvent.click(screen.getByRole("button", { name: "刷新状态" }));
	expect(refresh).toHaveBeenCalledTimes(1);
	expect(api.POST).not.toHaveBeenCalled();
	expect(api.PATCH).not.toHaveBeenCalled();
});

it("links the authentication problem to this agent's native chat", async () => {
	const openNative = vi.fn();
	api.GET.mockResolvedValue({ error: { code: "CHAT_AUTH_REQUIRED" } });
	mount(
		<PeerAgentSettings
			session={session}
			issue=""
			disabled={false}
			onBusyChange={() => {}}
			onRenamed={() => {}}
			onOpenSession={openNative}
		/>,
	);
	await userEvent.click(screen.getByRole("button", { name: "设置成员 开发" }));
	expect(await screen.findByText(/需要登录/)).toBeInTheDocument();
	await userEvent.click(screen.getByRole("button", { name: "打开原生会话登录" }));
	expect(openNative).toHaveBeenCalledTimes(1);
	expect(api.POST).not.toHaveBeenCalled();
});

it("keeps readiness and last delivery failure distinct and shows the observed reason", async () => {
	api.GET.mockResolvedValue({ data: { models: [], options: [] } });
	mount(
		<PeerAgentSettings
			session={session}
			issue=""
			disabled={false}
			onBusyChange={() => {}}
			onRenamed={() => {}}
			deliveryProblem={{ status: "上次未送达", detail: "客户端尚未登录。" }}
		/>,
	);
	await userEvent.click(screen.getByRole("button", { name: "设置成员 开发" }));
	expect(screen.getByText("可发送文字")).toBeInTheDocument();
	expect(screen.getByText("上次未送达")).toBeInTheDocument();
	expect(screen.getByText(/客户端尚未登录/)).toBeInTheDocument();
	expect(api.POST).not.toHaveBeenCalled();
});
