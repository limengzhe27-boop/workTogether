import React, { useEffect, useId, useRef } from 'react';

/** Native modal owns focus containment and Escape; return focus on unmount. */
export function Dialog({ title, onClose, children }: {
  title: string; onClose: () => void; children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current!;
    const trigger = document.activeElement as HTMLElement | null;
    dialog.showModal();
    return () => { dialog.close(); trigger?.focus(); };
  }, []);
  return <dialog ref={ref} className="modal-card" aria-labelledby={titleId}
    onCancel={e => { e.preventDefault(); onClose(); }}>
    <div className="dialog-heading">
      <h2 id={titleId}>{title}</h2>
      <button type="button" onClick={onClose} aria-label="关闭弹窗">关闭</button>
    </div>
    {children}
  </dialog>;
}
