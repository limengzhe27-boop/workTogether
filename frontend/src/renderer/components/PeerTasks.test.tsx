import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, it, expect, vi } from "vitest";
import { PeerTasks } from "./PeerTasks";
const api = vi.hoisted(() => ({ GET: vi.fn(), PUT: vi.fn(), POST: vi.fn() }));
vi.mock("../lib/api-client", () => ({ apiClient: api }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
it("shows ownership and creates an open task without impersonating an agent", async () => {
  api.GET.mockImplementation(async (path: string) => path.endsWith("/tasks") ? ({
    data: [
      {
        id: "t",
        goal: "检查刷新恢复",
        state: "claimed",
        owner: "codex-2",
        evidence: "",
        reviewer: "",
      },
    ],
  }) : ({data: []}));
  api.PUT.mockResolvedValue({ data: {} });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PeerTasks roomId="r" />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText(/团队任务/));
  await screen.findByText("检查刷新恢复");
  expect(screen.getByText(/codex-2/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("任务目标"), {
    target: { value: "检查边界输入" },
  });
  fireEvent.click(screen.getByRole("button", { name: "添加任务" }));
  await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1));
  expect(api.PUT.mock.calls[0][1].body).toMatchObject({
    goal: "检查边界输入",
    owner: "",
    state: "open",
  });
});

it("only offers candidate integration after independent acceptance", async () => {
  api.GET.mockImplementation(async (path: string) => path.endsWith("/tasks") ? ({
    data: [
      {
        id: "t",
        goal: "已完成任务",
        state: "submitted",
        owner: "codex-1",
        evidence: "tests passed",
        reviewer: "",
      },
    ],
  }) : ({data: []}));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PeerTasks roomId="r" epoch={2} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText(/团队任务/));
  await screen.findByText("已完成任务");
  fireEvent.click(screen.getByText("生成候选版本"));
  fireEvent.change(screen.getByLabelText("基线提交（完整提交编号）"), {
    target: { value: "a".repeat(40) },
  });
  fireEvent.change(screen.getByLabelText("候选名称"), {
    target: { value: "candidate" },
  });
  expect(screen.getByRole("button", { name: "生成候选分支" })).toBeDisabled();
});

it("returns an orphaned claim to the queue using the confirmed task revision", async () => {
	const task = { id: "orphan", goal: "构建并验收登录", state: "claimed", owner: "former-agent", reviewer: "", revision: 5 };
	let tasks = [task];
	api.GET.mockImplementation(async (path: string) => ({ data: path.endsWith("/tasks") ? tasks : [] }));
	api.POST.mockImplementation(async () => {
		tasks = [{ ...task, state: "open", owner: "", revision: 6 }];
		return { data: tasks[0] };
	});
	render(
		<QueryClientProvider client={new QueryClient()}>
			<PeerTasks roomId="r" epoch={2} />
		</QueryClientProvider>,
	);
	fireEvent.click(screen.getByText(/团队任务/));
	fireEvent.click(await screen.findByRole("button", { name: "解除认领" }));
	expect(api.POST).not.toHaveBeenCalled();
	expect(screen.getByRole("dialog", { name: "解除任务认领" })).toBeVisible();
	fireEvent.click(screen.getByRole("button", { name: "放回待认领" }));
	await screen.findByText(/待认领 · 尚未认领/);
	expect(api.POST).toHaveBeenCalledWith("/api/v1/peer-rooms/{roomId}/tasks/{taskId}/release", {
		params: { path: { roomId: "r", taskId: "orphan" } },
		body: { epoch: 2, revision: 5 },
	});
});

it("shows generated candidates in the manual check picker immediately", async () => {
	const base = "a".repeat(40);
	const commit = "b".repeat(40);
	const branch = "codex/integration-final";
	let generated = false;
	api.GET.mockImplementation(async (path: string) => {
		if (path.endsWith("/tasks")) {
			return { data: [{ id: "task", goal: "登录修复", state: "accepted", owner: "a", reviewer: "b", commit }] };
		}
		return { data: generated ? [{ branch, commit, checks: [] }] : [] };
	});
	api.POST.mockImplementation(async () => {
		generated = true;
		return { data: { branch, commit } };
	});
	render(
		<QueryClientProvider client={new QueryClient()}>
			<PeerTasks roomId="r" epoch={2} />
		</QueryClientProvider>,
	);
	fireEvent.click(screen.getByText(/团队任务/));
	await screen.findByText("登录修复");
	fireEvent.click(screen.getByText("生成候选版本"));
	fireEvent.change(screen.getByLabelText("基线提交（完整提交编号）"), { target: { value: base } });
	const name = screen.getByLabelText("候选名称");
	fireEvent.change(name, { target: { value: "bad name" } });
	expect(screen.getByRole("button", { name: "生成候选分支" })).toBeDisabled();
	fireEvent.change(name, { target: { value: "final" } });
	fireEvent.click(screen.getByText("候选版本与集成测试记录"));
	fireEvent.click(screen.getByRole("button", { name: "生成候选分支" }));
	await screen.findByRole("option", { name: `${branch} · ${commit.slice(0, 12)}` });
	expect(api.POST).toHaveBeenCalledWith("/api/v1/peer-rooms/{roomId}/integrate", {
		params: { path: { roomId: "r" } },
		body: { epoch: 2, base, name: "final" },
	});
});
