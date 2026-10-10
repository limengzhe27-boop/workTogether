import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

test('body, muted, primary, error and user-message text meet 4.5:1 contrast', async () => {
  const css = await readFile(new URL('../src/styles/workspace.css', import.meta.url), 'utf8');
  const colors = Object.fromEntries([...css.matchAll(/--([\w-]+):\s*#([\da-f]{6});/gi)].map(match => [match[1], match[2]]));
  const luminance = color => {
    const rgb = color.match(/../g).map(part => parseInt(part, 16) / 255)
      .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
    return .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2];
  };
  for (const [foreground, background] of [
    ['text', 'bg'], ['muted', 'surface'], ['muted', 'composer'],
    ['on-accent', 'accent'], ['on-accent', 'accent-hover'], ['error', 'error-bg'], ['on-user', 'user-bubble'],
  ]) {
    const values = [luminance(colors[foreground]), luminance(colors[background])];
    const ratio = (Math.max(...values) + .05) / (Math.min(...values) + .05);
    assert.ok(ratio >= 4.5, `${foreground} on ${background}: ${ratio}`);
  }
});
