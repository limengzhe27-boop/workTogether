import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

// Compile these leaf components (and the local modules they import) in memory;
// no DOM runner or generated files needed.
const srcRoot = new URL('../src/', import.meta.url);
const compiled = new Map();

function resolveLocal(specifier, fromFile) {
  const base = new URL(specifier, new URL(fromFile, srcRoot));
  for (const candidate of [base.href, base.href + '.tsx', base.href + '.ts', base.href + '/index.tsx']) {
    const path = new URL(candidate);
    if (existsSync(path)) return path.pathname.slice(srcRoot.pathname.length);
  }
  throw new Error(`Cannot resolve local module ${specifier} from ${fromFile}`);
}

async function moduleHref(filename) {
  if (compiled.has(filename)) return compiled.get(filename);
  const source = await readFile(new URL(filename, srcRoot), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    fileName: filename,
    compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  });
  const localSpecifiers = new Set(
    [...outputText.matchAll(/from (['"])(\.\.?\/[^'"]+)\1/g)].map(match => match[2]));
  const hrefs = new Map();
  for (const specifier of localSpecifiers) {
    hrefs.set(specifier, await moduleHref(resolveLocal(specifier, filename)));
  }
  const linked = outputText.replace(/from (['"])([^'"]+)\1/g,
    (_, quote, specifier) => 'from ' + JSON.stringify(
      hrefs.has(specifier) ? hrefs.get(specifier) : import.meta.resolve(specifier)));
  const href = 'data:text/javascript;base64,' + Buffer.from(linked).toString('base64');
  compiled.set(filename, href);
  return href;
}

async function component(filename) {
  return import(await moduleHref(filename));
}

const { MessageContent } = await component('MessageContent.tsx');
const { ProjectNavigation } = await component('ProjectNavigation.tsx');
const { TaskChat } = await component('TaskChat.tsx');
const renderMessage = text => renderToStaticMarkup(React.createElement(MessageContent, { text }));
const noop = () => {};
const navigationProps = {
  projects: [{ id: 'p1', name: 'Project One', path: '/p1' }, { id: 'p2', name: 'Project Two', path: '/p2' }],
  project: 'p1', taskId: 't1', busy: false,
  tasks: [{ id: 't1', title: 'Current task', status: 'ACTIVE' }, { id: 't2', title: 'Blocked task', status: 'BLOCKED' }],
  onProject: noop, onTask: noop, onNew: noop, onAdd: noop, onSearch: noop, onClose: noop, onSettings: noop,
};
const renderNavigation = props => renderToStaticMarkup(React.createElement(ProjectNavigation, { ...navigationProps, ...props }));

test('renders Markdown headings, lists and fenced code without executing code', () => {
  const html = renderMessage('## Summary\n\n**Done**\n\n- first\n- second\n\n```html\n<script>alert(1)</script>\n```');
  assert.match(html, /<h2>Summary<\/h2>/);
  assert.match(html, /<strong>Done<\/strong>/);
  assert.match(html, /<li>second<\/li>/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

test('does not render raw HTML, script or iframe from agent output', () => {
  const html = renderMessage('<script>alert(1)</script>\n\n<iframe src="https://example.com"></iframe>\n\n<img src=x onerror=alert(1)>\n\nSafe text');
  assert.doesNotMatch(html, /<(script|iframe|img)\b|onerror=/);
  assert.match(html, /Safe text/);
});

test('keeps safe external links isolated and strips unsafe URL schemes', () => {
  const html = renderMessage('[Safe](https://example.com) [Bad](javascript:alert) [Data](data:text/html,unsafe)');
  assert.match(html, /href="https:\/\/example.com" target="_blank" rel="noopener noreferrer"/);
  assert.doesNotMatch(html, /href="(?:javascript|data):/);
});

test('does not automatically load model-supplied image URLs', () => {
  const html = renderMessage('![evidence](https://example.com/private-tracker.png)');
  assert.doesNotMatch(html, /<img\b|src=|private-tracker/);
  assert.match(html, /图片未自动加载：evidence/);
});

test('project tree renders a selected task once and leaves other projects collapsed', () => {
  const html = renderNavigation({});
  assert.equal((html.match(/Current task<\/span>/g) || []).length, 1);
  assert.match(html, /aria-current="page"/);
  assert.match(html, /aria-expanded="true"/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /aria-label="在 Project One 下新建会话"/);
  assert.match(html, /aria-label="需要处理"/);
  assert.doesNotMatch(html, /未读/);
});

test('empty workspace keeps add-project, search and mobile settings reachable', () => {
  const html = renderNavigation({ projects: [], project: '', tasks: [], taskId: '' });
  assert.match(html, /添加本地项目后开始协作/);
  assert.match(html, /aria-label="添加项目"/);
  assert.match(html, /aria-label="搜索当前项目任务"/);
  assert.match(html, /aria-label="工作区 Agent 设置"/);
});

const chatAgent = {
  id: 'codex', name: 'Codex', type: 'cli', roles: ['chat'], color: '#6366f1',
  avatar: '🤖', description: '', enabled: true, ready: true,
};
const renderChat = props => {
  // TaskChat polls in an effect and uses useLayoutEffect; server rendering only
  // checks the static composer markup, so silence the expected SSR warnings.
  const original = console.error;
  console.error = () => {};
  try {
    return renderToStaticMarkup(React.createElement(TaskChat, {
      taskId: 't1', projectId: 'p1', agents: [chatAgent], onPending: noop, onDetails: noop,
      visible: true, ...props,
    }));
  } finally {
    console.error = original;
  }
};

test('chat composer exposes file context, session write grant and rounds controls', () => {
  const html = renderChat({});
  assert.match(html, /引用文件/);
  assert.match(html, /可在项目里工作/);
  assert.match(html, /aria-label="选择对话 Agent"/);
  assert.match(html, /轮次/);
  assert.match(html, /aria-label="发送消息"/);
});

test('chat composer explains the no-ready-agent case instead of offering a dead send', () => {
  const html = renderChat({ agents: [{ ...chatAgent, ready: false }] });
  assert.match(html, /没有就绪成员/);
  assert.match(html, /disabled=""/);
});

test('chat composer does not grant project write before the session state loads', () => {
  const html = renderChat({});
  const toggles = html.match(/<input type="checkbox"[^>]*>/g) || [];
  assert.ok(toggles.every(tag => !tag.includes('checked')), 'write grant must default to off in the UI');
});

test('chat composer marks an agent whose local CLI is missing', () => {
  const html = renderChat({ providers: { codex: { available: false, executable: 'codex' } } });
  assert.match(html, /本机未找到 codex/);
  assert.match(html, /is-unavailable/);
  assert.match(html, /aria-label="本机 CLI 未找到"/);
});

test('chat composer leaves available CLIs unmarked', () => {
  const html = renderChat({ providers: { codex: { available: true, executable: 'codex' } } });
  assert.doesNotMatch(html, /本机未找到/);
  assert.doesNotMatch(html, /is-unavailable/);
});
