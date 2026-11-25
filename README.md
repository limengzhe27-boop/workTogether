# WorkTogether

WorkTogether 是面向本地项目的多 Agent 桌面工作台。每位成员保有独立的客户端会话，同一项目中的成员可以并行接收消息、相互交接、认领任务和交叉验收。

## 快速使用

1. 打开本地 Git 项目，进入项目群聊。
2. 从输入框旁的「＋」添加 Agent。一个项目可以有多个同类会话。
3. 发送新消息，按成员查看回复、执行记录和审批。需要看完整上下文时可打开原生会话。
4. 在团队任务里查看负责人、产物和验收结论。候选分支的测试结果与任务验收分别记录。

使用各客户端自己的安装、登录与额度。具体工具和权限由当前客户端、模型和会话设置决定。任意编码任务的无人值守交付仍需要按项目验证。

## 从源码运行

需要 Go（版本见 [go.mod](backend/go.mod)）、Node.js 24 和可用的 Git。macOS 打包还需要原生构建工具。依赖安装与开发环境详见 [开发指南](docs/development.md)。

```sh
npm ci --prefix frontend
npm ci --prefix packages/product-ui
npm --prefix frontend run dev
```

本机构建桌面应用可运行 `npm --prefix frontend run build`，产物在 `frontend/out/`。本地 macOS 安装使用 `WorkTogether Local.app`。

```sh
go -C backend test -p 4 ./...
npm --prefix frontend run typecheck
npm --prefix frontend test -- --maxWorkers=3
```

旧 Python/Web 版保留在 [legacy-python-web](https://github.com/limengzhe27-boop/workTogether/tree/legacy-python-web)，当前桌面版在 `main`。

## 上游

桌面版从 Agent Orchestrator 原生架构派生，初始基线为 `033d597032e448a0ea16c298350e36b099f74106`。保留上游的许可证和署名。详见 [架构说明](docs/architecture.md)、[上游仓库](https://github.com/OrchestratorInc/agent-orchestrator) 和 [许可证](LICENSE)。
