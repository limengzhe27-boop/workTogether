import React from 'react';
import Markdown from 'react-markdown';

/** Treat model output as text/Markdown, never as executable HTML or remote media. */
export function MessageContent({ text }: { text: string }) {
  return <div className="prose markdown"><Markdown skipHtml components={{
    a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    img: ({ alt }) => <span className="small">[图片未自动加载{alt ? '：' + alt : ''}]</span>,
  }}>{text}</Markdown></div>;
}
