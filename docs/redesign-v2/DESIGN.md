# WorkTogether redesign v2 — 设计提案

日期：2026-09-22。状态：用户已授权实施，当前正在构建和验证。保留根目录旧 DESIGN.md
作为历史；实施本次改版时以本文件为准。产品目标：打开项目、说明目标、看双方
工作与成果，在需要时作决定。主要开发由 Antigravity 承担，Codex 集中审查。

## 1. Visual Theme & Atmosphere

风格：Minimal Pure 的克制与 Warm Professional 的可读性。浅色默认，暖灰导航、
白色工作区、深灰正文、蓝色主操作。关键词：清楚、安静、可信、连续、可掌控。
这是工作工具，不采用营销首页的 Hero、装饰图片或滚动特效。
交互档位建议 L1：CSS 轻反馈，无动画库；提交本方案时一并确认，当前不写前端。
角色以 C/A 字母头像和名称区分，颜色仅辅助；角色标签不暗示能力或连接已就绪。

## 2. Color Palette & Roles

```css
:root {
  --bg:#f7f8fa; --bg-rgb:247,248,250;
  --surface:#ffffff; --surface-rgb:255,255,255;
  --surface-alt:#f0f2f5; --surface-hover:#e8edf4;
  --border:#dce1e8; --border-hover:#9aa7b8;
  --text:#202630; --text-secondary:#505b6b; --text-tertiary:#606d7e;
  --accent:#245fc7; --accent-rgb:36,95,199;
  --accent-hover:#194b9e; --accent-active:#153d80; --on-accent:#ffffff;
  --success:#187046; --success-rgb:24,112,70;
  --warning:#8a5709; --warning-rgb:138,87,9;
  --error:#b02e38; --error-rgb:176,46,56;
  --shadow-rgb:32,38,48;
}
```

主操作使用 accent；成功/失败用于真实执行结果。任务暂停用中性灰，待用户处理
用琥珀色。普通文字对比度至少 4.5:1；按钮边界、焦点等非文本至少 3:1。
不用彩色渐变、发光或颜色本身表达状态。以后暗色主题复用语义变量，不在本轮叠加工作量。

## 3. Typography Rules

```css
/* 联网开发预览可用；生产优先自托管，字体加载失败不能阻塞本地工具。 */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700&display=swap');
body { font-family:'Noto Sans SC','PingFang SC','Microsoft YaHei',sans-serif;
  font-size:15px; line-height:1.7; letter-spacing:.02em; color:var(--text); }
code,pre { font-family:'SFMono-Regular',Consolas,monospace; letter-spacing:0; }
```

| 层级 | 字号 | 字重 | 行高 |
|---|---|---|---|
| 项目首页提问 | 28px | 600 | 1.4 |
| 任务标题 | 20px | 600 | 1.5 |
| 区块标题 | 15px | 600 | 1.7 |
| 沟通正文 | 16px | 400 | 1.75 |
| 导航和按钮 | 15px | 500 | 1.7 |
| 时间、角色说明 | 13px | 400 | 1.7 |

标题、正文均不加渐变和投影；不用等宽字体排普通中文，不用超小字号挤进更多数据。

## 4. Component Stylings

```css
.button,.nav-item,.text-link { min-height:40px; border-radius:8px;
  font:inherit; cursor:pointer; transition:background-color 140ms,border-color 140ms; }
.button { padding:7px 14px; border:1px solid var(--border);
  color:var(--text); background:var(--surface); }
.button:hover { background:var(--surface-hover); border-color:var(--border-hover); }
.button:active { background:var(--surface-alt); }
.button.primary { background:var(--accent); border-color:var(--accent); color:var(--on-accent); }
.button.primary:hover { background:var(--accent-hover); }
.button.primary:active { background:var(--accent-active); }
:is(button,a,input,textarea,select,summary):focus-visible {
  outline:2px solid var(--accent); outline-offset:3px; }
:is(button,input,textarea,select):disabled { opacity:.55; cursor:not-allowed; }
.button:disabled:hover { background:var(--surface); border-color:var(--border); }
.button.primary:disabled:hover { background:var(--accent); border-color:var(--accent); }
.nav-item { display:flex; align-items:center; padding:8px 12px;
  color:var(--text-secondary); background:transparent; border:0; }
.nav-item:hover { background:var(--surface-hover); }
.nav-item:active,.nav-item[aria-current=page] { background:var(--surface-alt); color:var(--text); }
.text-link { color:var(--accent); text-underline-offset:4px; }
.text-link:hover { color:var(--accent-hover); text-decoration:underline; }
.text-link:active { color:var(--accent-active); }
.text-link[aria-disabled=true] { color:var(--text-tertiary); cursor:not-allowed; }
.card { border:1px solid var(--border); border-radius:12px; padding:20px; background:var(--surface); }
button.card { text-align:left; font:inherit; cursor:pointer; }
button.card:hover { border-color:var(--border-hover); background:var(--surface-hover); }
button.card:active { background:var(--surface-alt); }
button.card:disabled { opacity:.55; cursor:not-allowed; }
button.card:disabled:hover { background:var(--surface); border-color:var(--border); }
.badge { padding:2px 8px; border-radius:6px; background:var(--surface-alt); color:var(--text-secondary); }
.badge.warning { background:rgba(var(--warning-rgb),.08); color:var(--warning); }
.badge.success { background:rgba(var(--success-rgb),.08); color:var(--success); }
.badge.error { background:rgba(var(--error-rgb),.08); color:var(--error); }
.composer { padding:16px; background:var(--surface); border:1px solid var(--border); border-radius:12px; }
.composer:hover { border-color:var(--border-hover); }
.composer:focus-within { border-color:var(--accent); box-shadow:0 0 0 3px rgba(var(--accent-rgb),.1); }
.composer textarea { width:100%; min-height:112px; max-height:280px; resize:vertical;
  font:inherit; color:var(--text); background:var(--surface); border:0; }
```

普通消息无外框，以角色/时间/内容建立层次。只有计划、验收、需决策问题和最终
交付用卡片。主画面每个状态一个主操作；状态标签不是按钮。
菜单和模态框必须键盘可操作、有名称、管理焦点，Esc 关闭并归还焦点。
发送失败保留草稿，错误放在操作附近；不用系统 alert 弹窗。

## 5. Layout Principles

导航 240px，右侧进度 288px，中间 minmax(0,1fr)。中间正文宽度上限 800px，
左右 padding 32px。顶部 56px；间距采用 4/8/12/16/24/32/48px。
桌面主体填满高度，导航、中间和右栏可独立滚动，不使用全页滚动劫持。

### 页面 A：项目首页

- 左侧顶部项目切换，下方唯一“新任务”入口和最近任务列表，底部“连接与设置”。
- 主区顶部项目名和简短说明，目录路径放项目信息抽屉。
- 主区上部直接显示“这个项目，你想完成什么？”与 3–5 行需求输入。
- 输入下方展示 Codex/Antigravity 的实际连接状态，以及执行方式。
- 主按钮“创建任务”；不得承诺已经启动模型。自动启动接口完成后才可改成“开始规划”。
- 下方最多 5 个最近任务，无任务则一句说明；不重复放创建按钮。
- 无项目时仅给添加项目引导，添加成功保留需求草稿。
- 演示入口放设置里的帮助页，不占主导航或主按钮。

### 页面 B：任务工作区

```text
项目 / 任务导航    任务标题 · 当前实际状态                 任务概览
                  沟通与进展 | 交付物                     当前执行者
最近任务          用户需求                               阶段与步骤
                  Codex 的方案卡                          验收进度
                  Antigravity 的建议/验收                 连接状态
                  需要你处理的问题（如有）                 执行详情入口
                  阶段成果
                  -----------------------------------
连接与设置        补充当前任务…          [发送补充]
```

- 右栏仅显示重点，不和中间重复整份计划。步骤可展开验收标准。
- 新消息时间顺序向下。用户在读历史时不强制滚到底，显示“有新进展”。
- 没有新事件时保持滚动、焦点、输入和展开状态，禁止轮询替换整个 DOM。
- 只展示实际存储的方案/报告/消息；没有真实对话记录时称为“进展”，不伪造聊天。
- 模型分析过程不默认全部显示，优先展示可交付结论及证据。

### 页面 C：交付物

列出实际修改摘要、版本、验收覆盖、问题与证据；点击查看详情。
没有文件 diff 接口时仅展示已有交接摘要，不伪造文件数量或 Diff。
完成卡片区分“验收已通过”和“用户已确认”；后者需要后端记录才可展示。
无法执行的检查明确为“未验证”，不能写成全量通过。

### 页面 D：连接与设置

展示每个 provider 的 CLI 存在、登录待验证、可调用验证结果、最近检查时间。
状态至少包含未配置/未验证/可调用/调用失败，不凭 PATH 存在显示“已连接”。
手动交接是明确模式。讨论/修复上限在后端支持配置前只读显示。
运行日志、会话 ID、原始 Token 和 JSON 调试入口统一放“执行详情”。

### 状态与主要动作

| 实际情况 | 说明 | 主操作 |
|---|---|---|
| 新任务未派发 | 需求已保存，尚未执行 | 开始规划（协调派发与运行） |
| 正在执行 | Codex 正在规划/实现；只有真实 RUNNING 才这样写 | 暂停后续工作（次级） |
| Antigravity 手动模式 | 等待交接，尚未自动发送 | 复制交接内容；导入结果为次级 |
| 用户需决策 | 给出具体问题、选项与影响 | 提交决定（待 decision API） |
| 验收失败 | 明确失败项、修复次数和证据 | 修复并复验（待有限调度） |
| 未知进程状态 | 无法确认上次执行是否结束 | 检查执行情况；不直接给再次运行 |
| 已暂停 | 后续步骤已暂停，在途工作可能仍运行 | 继续 |
| 完成 | 展示成果与验收范围 | 查看交付 |

### 当前任务输入的硬性语义

首页输入创建新任务；任务页输入只能补充当前任务，绝不调用 task-add。
需要 message API 保存 task_id、message_id、内容和 delivered/queued/applied 状态。
在途时先排队并停止后续自动派发；界面显示“已保存，尚未交给 Agent”，不是“已执行”。
修改需求影响验收时必须生成新 requirement/plan revision，作废旧待执行派发，
将受影响在途结果标记待核对。关联报告及派发必须绑定 revision，不能悄悄重写原需求。
这些接口未实现前不展示可发送的聊天输入，可提供明确标注的“本地草稿”。
Enter 换行，Ctrl/Cmd+Enter 发送；中文输入法组合输入不触发发送。

## 6. Depth & Elevation

主体和普通卡片不加阴影，以边界和留白分层。菜单/输入聚焦可以轻投影，模态
使用单层遮罩，不叠多层悬浮窗口。

```css
.popover { box-shadow:0 8px 24px rgba(var(--shadow-rgb),.1); }
.dialog { box-shadow:0 16px 48px rgba(var(--shadow-rgb),.16); }
.scrim { background:rgba(var(--shadow-rgb),.25); }
```

## 7. Animation & Interaction

L1，CSS only。hover 140ms，面板进入 160ms，首次页面进入 180ms。
不对轮询更新重播入场动画，不用假打字动画模拟模型进度。

```css
@keyframes enter { from { opacity:0; transform:translateY(4px); }
  to { opacity:1; transform:translateY(0); } }
.page-enter { animation:enter 180ms ease-out both; }
.panel-enter { animation:enter 160ms ease-out both; }
@media(prefers-reduced-motion:reduce) {
  *,*::before,*::after { animation:none!important; transition:none!important;
    scroll-behavior:auto!important; }
}
```

进度显示已完成步骤数和阶段，不将步骤完成比例写成整体真实耗时百分比。
实时状态播报用 polite live region，只读结果更新不抢焦点。

## 8. Do's and Don'ts

Do：
- 每页明确当前项目和任务身份。
- 每项主要操作写实际结果，如“复制交接内容”。
- 依据服务端真实状态显示运行、排队和失败。
- 用方案、问题、成果承载沟通。
- 草稿随项目/任务隔离并保存；后端失败不清空输入。
- 错误包含可执行的下一步，未知状态给检查入口。

Don't：
- 不使用渐变大按钮、发光背景、装饰性 Emoji。
- 不在首页放巨大欢迎语或重复创建入口。
- 不把 JSON、PID、原始状态码当默认正文。
- 不将 dispatch ACTIVE 等同于 provider RUNNING。
- 不让模型每次轮询生成一句状态摘要。
- 不假装已有自由聊天、自动接入、决策审批或需求修订能力。
- 不用自动刷新覆盖输入或切换用户选中的任务。
- 不将完成模型调用等同于完成整个任务。
- 不通过“继续”偷偷重放未知执行。
- 不维护两个独立版本的前端实现。

### Antigravity 实施顺序与验收关卡

**P0 单一前端与契约修复**：以 web/src 的 React/TypeScript 为唯一源码，
补齐 Vite 构建配置与必要依赖，构建输出到 worktogether/static；该目录只放构建
产物，不手改第二套 app.js。修复事件 kind/data 对齐、真实 report 展示、请求
乱序造成的串任务、输入草稿被轮询清除；未知事件可读回退，不调用未定义字段。
验收：可从干净依赖环境构建，生产 URL 显示的就是同一份源码，无需额外维护手写版。

**P1 新首页与任务工作区**：按上述布局实现中文界面、进展/交付视图和手动交接抽屉。
保留真实接口能力；尚不支持的交互给说明，不做假按钮。桌面和窄屏各截图。
验收：创建、切换、派发、运行入口、查看报告、暂停恢复均通过 UI 操作验证。

**P2 当前任务补充与有限推进**：先实现 message/revision/decision（如需要）协议，
再接任务输入框。增加用户触发的“开始规划”协调入口，服务端负责去重和调用。
只在明确授权有限自动推进后安排后续步骤，保留讨论和修复上限。
验收：提交补充不会创建任务；刷新后消息仍在；未消费标识准确；旧 revision 不批准
新要求；重复点击不重复执行；暂停后没有新派发。此阶段不能以纯 UI 修改代替后端。

**P3 连接与环境完善**：验证 Antigravity 真实接口、固定版本环境和 worktree，
逐步替换手动交接。不能因接入暂时阻塞就停做 P0/P1。

每个阶段写清：改了什么、实际测试、截图、已知限制。Codex 集中审查，不要求
每个组件都回报，也不调用 Codex 来生成前端。当前文档不授权安装系统级 CLI 或登录。

## 9. Responsive Behavior

| 宽度 | 布局 |
|---|---|
| ≥1280px | 三栏，240px / 中间 / 288px |
| 960–1279px | 导航 220px + 主区；右侧详情用抽屉 |
| <960px | 主区单栏；项目导航和详情均为抽屉 |
| ≤600px | 主区 padding 16px；操作不溢出，触摸目标至少 44px |

```css
.workspace { display:grid; grid-template-columns:240px minmax(0,1fr) 288px;
  height:100dvh; background:var(--bg); }
.main { min-width:0; min-height:0; display:flex; flex-direction:column; }
.main-scroll { overflow:auto; min-height:0; flex:1; }
.reading { width:100%; max-width:800px; margin:auto; padding:24px 32px; }
.composer-wrap { flex:none; padding:12px 24px max(12px,env(safe-area-inset-bottom)); }
@media(max-width:1279px) {
  .workspace { grid-template-columns:220px minmax(0,1fr); }
  .inspector { display:none; }
}
@media(max-width:959px) {
  .workspace { grid-template-columns:minmax(0,1fr); }
  .sidebar { display:none; }
}
@media(max-width:600px) {
  .reading { padding:20px 16px; }
  .composer-wrap { padding-inline:12px; }
  button,.nav-item,.text-link { min-height:44px; }
}
```

以上隐藏的面板必须有可见抽屉入口。长路径、代码在自身容器换行或横向滚动，
不撑宽页面。至少实测 1440×900、1024×768、390×844；弹出键盘不遮挡发送。
