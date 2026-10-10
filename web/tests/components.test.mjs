import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

// Compile these leaf components in memory; no DOM runner or generated files needed.
async function component(filename) {
  const source = await readFile(new URL('../src/' + filename, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    fileName: filename,
    compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  });
  const linked = outputText.replace(/from (['"])([^'"]+)\1/g,
    (_, quote, specifier) => 'from ' + JSON.stringify(import.meta.resolve(specifier)));
  return import('data:text/javascript;base64,' + Buffer.from(linked).toString('base64'));
}

const { MessageContent } = await component('MessageContent.tsx');
const { ProjectNavigation } = await component('ProjectNavigation.tsx');
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
  assert.match(html, /aria-label="需要处理"/);
  assert.doesNotMatch(html, /未读/);
});

test('empty workspace keeps add-project, search and mobile settings reachable', () => {
  const html = renderNavigation({ projects: [], project: '', tasks: [], taskId: '' });
  assert.match(html, /添加本地项目后开始协作/);
  assert.match(html, /class="new-task" disabled=""/);
  assert.match(html, /添加项目/);
  assert.match(html, /aria-label="搜索当前项目任务"/);
  assert.match(html, /aria-label="工作区 Agent 设置"/);
});
