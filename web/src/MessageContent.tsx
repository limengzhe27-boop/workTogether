import React from 'react';
import Markdown from 'react-markdown';

function formatReadableMarkdown(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed || trimmed.includes('\n') || trimmed.length <= 85) {
    return raw;
  }
  const sentences = trimmed
    .split(/(?<=[。！？])\s*/)
    .map(s => s.trim())
    .filter(Boolean);
  if (sentences.length <= 1) {
    return raw;
  }
  if (sentences.length === 2) {
    return `${sentences[0]}\n\n${sentences[1]}`;
  }
  return `${sentences[0]}\n\n${sentences.slice(1).map(s => `- ${s}`).join('\n')}`;
}

/** Treat model output as text/Markdown, never as executable HTML or remote media. */
export function MessageContent({ text }: { text: string }) {
  const formatted = formatReadableMarkdown(text);
  return <div className="prose markdown"><Markdown skipHtml components={{
    a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    img: ({ alt }) => <span className="small">[图片未自动加载{alt ? '：' + alt : ''}]</span>,
  }}>{formatted}</Markdown></div>;
}
