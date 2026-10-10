# WorkTogether · Codex 工作界面参考规范

版本 3 · 2026-10-10。用户明确要求尽量对齐 Codex，随后提供完整深色截图并要求据此修改；该截图是本轮视觉基准，替代 v2 浅色方向。保留先前确认的 L1 轻动效。

参考优先级：用户截图 > [官方项目与对话导航示意](https://learn.chatgpt.com/docs/features) > 旧界面。截图约 2040×1540 像素，按桌面逻辑尺寸解释比例，不把高分辨率截图的像素当作 CSS 字号。截图不纳入公开仓库。旧后端架构记录见 [design-legacy-v1.md](docs/design-legacy-v1.md)。

## 1. Visual Theme & Atmosphere

Codex-style dark workspace：石墨黑、平面层级、宽阅读区、弱分隔线、项目树、底部大圆角输入。结构分为四块：顶部工作栏、左侧窄工具轨、项目/任务栏、主对话区。主内容不是卡片仪表盘。
保留 WorkTogether 品牌；不冒充官方产品。不复制 macOS 红黄绿窗控、虚构通知、账号身份、语音按钮、宠物或未接入的文件上传。所有显示的操作均有真实功能。

## 2. Color Palette & Roles

以截图视觉采样估值为准，并非从 Codex 私有代码获取：
```css
:root {
  color-scheme: dark;
  --bg: #181818; --surface: #202020; --surface-alt: #303030;
  --surface-hover: #383838; --rail: #252727; --chrome: #262f36;
  --composer: #353535; --border: #333333; --border-hover: #626262;
  --text: #ededed; --secondary: #cccccc; --muted: #a2a2a2;
  --accent: #3072d2; --accent-hover: #3375d5; --on-accent: #ffffff;
  --user-bubble: #1b4079; --on-user: #eef4ff;
  --error: #ffb4b4; --error-bg: #382526;
  --shadow-rgb: 0,0,0; --accent-rgb: 48,114,210; --bg-rgb: 24,24,24;
}
```
蓝色主要用于用户气泡与发送按钮。选中任务使用灰色底而非蓝色描边。历史失败不涂满首屏，当前阻塞保留高对比入口。注册表中的用户自选颜色属于数据，不作为页面主题色。

## 3. Typography Rules

中文优先字体栈：'Noto Sans SC','PingFang SC','Microsoft YaHei',sans-serif。字体引用：
```css
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600&display=swap');
```
离线使用本机回退。正文 16px / 1.8；侧栏与控件 15px / 1.7；辅助标签 12–13px；顶部标题 16px / 1.5；首页标题 28px。字距 .02em，字重 400/500/600，代码采用 SFMono-Regular/Consolas。
H1/H2/H3 均无渐变、无投影；正文按 Markdown 显示真实加粗、列表、链接和代码。忽略原始 HTML，不自动加载远程图片。禁止全大写装饰标签、伪造时间和截图正文占位。

## 4. Component Stylings

```css
button { min-height:44px; border:1px solid var(--border); border-radius:8px; background:var(--surface); color:var(--text); }
button:hover { background:var(--surface-hover); }
button:active { background:var(--surface-alt); }
button:disabled { opacity:.45; cursor:not-allowed; }
:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
.icon-button { width:44px; padding:10px; border-color:transparent; background:transparent; display:inline-flex; align-items:center; justify-content:center; }
.task-link { background:transparent; border-color:transparent; border-radius:10px; }
.task-link[aria-current=page] { background:var(--surface-alt); }
.user-message { width:fit-content; max-width:84%; margin-left:auto; background:var(--user-bubble); color:var(--on-user); border-radius:22px; padding:12px 18px; }
.chat-composer { background:var(--composer); border:1px solid var(--border); border-radius:24px; padding:14px; }
.chat-composer:focus-within { border-color:var(--border-hover); }
.send-button { background:var(--accent); border-radius:50%; }
.card { background:var(--surface); border:1px solid var(--border); border-radius:12px; }
.card:hover { border-color:var(--border-hover); }
a { color:var(--secondary); text-decoration:underline; text-underline-offset:3px; }
a:hover { color:var(--text); }
.badge { background:var(--surface-alt); color:var(--secondary); border-radius:6px; }
```
图标统一 lucide-react 20–22px，描边 1.7；不混用 Emoji。Agent 回复无独立气泡、没有彩色左边线，身份小标题和细线分隔，底部可复制正文。
弹窗使用原生 dialog（焦点锁定、Escape、焦点归还）；设置、团队、任务搜索复用相同暗色规范。折叠使用原生 details/summary。

## 5. Layout Principles

桌面网格：54px 工具轨 + 246px 项目栏 + minmax(0,1fr) 对话；48px 顶部工作栏横跨全宽。对话区尽量用满剩余宽度，最大内容宽 1240px，左右留白 24–32px。结构比例接近截图的约 5% 工具轨、23% 项目栏、72% 对话。
项目为文件夹行，当前项目下缩进显示任务；左上“新任务”，搜索图标打开当前项目任务搜索。侧栏支持折叠，折叠后对话扩宽。
主区不再保留常驻双标签栏：顶部“任务详情”图标切换详情；进度和下一步压为一条轻量栏。模型授权流程与原 API 保持不变。
底部输入框内部：上半部多行文字，下半部“团队”入口、仅交流说明、接收成员选择、圆形发送。输入框和正文同宽、固定在 flex 面板底部，不盖住正文。
间距梯度 4/8/12/16/24/32；用户消息与 Agent 回复间 28–36px，正文不做浮动卡片。

## 6. Depth & Elevation

正文和侧栏基本无阴影，通过 #181818/#202020/#303030 分层。只有弹窗和手机抽屉使用 0 16px 40px rgba(var(--shadow-rgb),.35)。不加光晕、毛玻璃或背景渐变。工作栏略带冷灰色，区别于纯灰内容区。

## 7. Animation & Interaction

L1，CSS only，无额外动效依赖：
```css
button { transition:background 140ms,border-color 140ms; }
@keyframes fadeIn { from { opacity:0; } to { opacity:1; } }
.page-enter, dialog[open] { animation:fadeIn 180ms ease-out; }
@media(prefers-reduced-motion:reduce) {
  *,*::before,*::after { animation:none!important; transition:none!important; scroll-behavior:auto!important; }
}
```
不做滚动 reveal、跳动、自动打字或假忙碌。消息只有初次加载、自己发送或靠近底部时跟随；向上阅读时给新动态入口。
项目切换隔离任务；搜索只读；复制不执行代码；“仅交流”不授权开发；讨论/执行仍需原确认和预算说明。调用中不替用户关闭权限检查。
真实时间字段存在才显示时间分隔；不添加“用时 xx 秒”假数据。

## 8. Do’s / Don’ts

Do：

1. 以截图的四区结构与深灰层级为基准。
2. 对话作为主要工作面，保持足够阅读宽度。
3. 左侧呈现项目与任务层级，选中行低调高亮。
4. Agent 身份、当前阻塞、审批和额度说明保持真实。
5. 保留草稿、项目隔离、手动交接、日志、恢复与重试。
6. 所有图标按钮有名称、提示和键盘焦点。

Don’t：

1. 不再沿用浅色商务卡片风。
2. 不把浅色 CSS 简单反色了事。
3. 不冒充 Codex 官方品牌或用户身份。
4. 不造语音、宠物、文件上传、反馈投票等空按钮。
5. 不复制截图里的私人项目或对话作为示例数据。
6. 不把 Agent 配置就绪显示为在线/授权已就绪。
7. 不把发送消息改为自动开发。
8. 不自动开启云端 Agent。
9. 不引入虚假日期、进度、未读蓝点或运行耗时。
10. 不在视觉改版中改变后端调度或清理用户任务。

## 9. Responsive Behavior

>=960px 展示完整工作栏+工具轨+项目树；601–959px 默认折叠项目栏，以抽屉打开；<=600px 隐藏工具轨，由顶部导航按钮打开项目抽屉。抽屉锁定焦点、Escape 关闭，主区与顶部在打开时 inert。
移动端正文 15–16px、16px 留白、输入框 20px 圆角；图标点击区至少 44×44px。整体 100dvh，正文独立滚动；小屏不出现横向溢出。模态框宽 calc(100vw - 24px)，内部表单单列。
验收：1046×653、1440×900、390×844；确认底部输入可见、导航收放、项目切换、搜索、复制、草稿与授权入口。对照截图记录对齐点和有意差异。仅本地假服务可用于自动化发送测试，不触发真实 Agent。
