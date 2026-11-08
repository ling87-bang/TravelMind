'use client';

import { memo, useCallback, useId, useRef, useState } from 'react';
import type { Conversation } from '@/lib/types';
import { cx, formatTime } from '@/lib/utils';

interface ConversationItemProps {
  conversation: Conversation;
  active: boolean;
  onSelect: (id: string) => void;
  onStatusChange: (id: string, status: 'active' | 'archived') => Promise<void>;
}

function ConversationItem({ conversation, active, onSelect, onStatusChange }: ConversationItemProps) {
  const [pending, setPending] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 });
  const menuId = useId();
  const dialogTitleId = useId();
  const dialogDescriptionId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuActionRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const archived = conversation.status === 'archived';
  const actionLabel = archived ? '恢复会话' : '归档会话';
  const handleStatusChange = async () => {
    if (pending) return;
    dialogRef.current?.close();
    menuRef.current?.hidePopover();
    setPending(true);
    try {
      await onStatusChange(conversation.id, archived ? 'active' : 'archived');
    } finally {
      setPending(false);
    }
  };
  const handleClick = useCallback(() => {
    onSelect(conversation.id);
  }, [conversation.id, onSelect]);
  const positionMenu = () => {
    const bounds = triggerRef.current?.getBoundingClientRect();
    if (!bounds) return;
    setMenuPosition({
      top: bounds.bottom + 72 < window.innerHeight ? bounds.bottom + 6 : Math.max(8, bounds.top - 64),
      left: Math.max(8, Math.min(bounds.right - 160, window.innerWidth - 168)),
    });
  };

  return (
    <div
      className={cx(
        'flex items-center gap-2 rounded-[6px] bg-white/50 p-3.5 transition-all duration-200 hover:bg-white',
        'border-l-[3px] border-l-transparent',
        active &&
          'border-l-accent bg-white shadow-[0_2px_8px_rgba(0,0,0,0.05)]',
      )}
    >
      <button type="button" onClick={handleClick} className="min-w-0 flex-1 text-left">
        <div className="mb-1 truncate text-sm font-medium text-text-main">
          <i className="fa-solid fa-map-pin mr-1 text-[10px] text-accent" />
          {conversation.title || '未知行程'}
        </div>
        <div className="flex items-center gap-1 text-[11px] text-text-light">
          <i className="fa-regular fa-clock text-[10px]" />
          {formatTime(conversation.updated_at || conversation.created_at)}
        </div>
      </button>
      <button ref={triggerRef} type="button" title="更多操作" aria-label={`更多操作 ${conversation.title}`}
        aria-haspopup="menu" aria-expanded={menuOpen} aria-controls={menuId}
        popoverTarget={menuId} disabled={pending} onClick={positionMenu}
        className="shrink-0 rounded p-2 text-sm text-text-light hover:bg-primary/10 hover:text-primary disabled:opacity-40">
        <i aria-hidden className={pending ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-ellipsis'} />
      </button>
      <div ref={menuRef} id={menuId} popover="auto" role="menu" aria-label="会话操作"
        onToggle={(event) => {
          const open = event.newState === 'open';
          setMenuOpen(open);
          if (open) menuActionRef.current?.focus();
        }}
        style={{ inset: 'auto', margin: 0, top: menuPosition.top, left: menuPosition.left }}
        className="fixed w-40 rounded-lg border border-black/10 bg-white p-1.5 text-sm text-text-main shadow-lg">
        <button ref={menuActionRef} type="button" role="menuitem" disabled={pending}
          onClick={() => {
            menuRef.current?.hidePopover();
            if (archived) void handleStatusChange();
            else dialogRef.current?.showModal();
          }}
          className="flex w-full items-center gap-2 rounded px-3 py-2 text-left hover:bg-primary/10 focus:bg-primary/10 focus:outline-none">
          <i aria-hidden className={archived ? 'fa-solid fa-arrow-rotate-left' : 'fa-solid fa-box-archive'} />
          {actionLabel}{archived ? '' : '…'}
        </button>
      </div>
      <dialog ref={dialogRef} aria-labelledby={dialogTitleId} aria-describedby={dialogDescriptionId}
        onClose={() => triggerRef.current?.focus()}
        className="m-auto w-[calc(100%-2rem)] max-w-sm rounded-xl border border-black/10 bg-white p-6 text-text-main shadow-xl backdrop:bg-black/30">
        <h3 id={dialogTitleId} className="font-serif text-lg font-semibold">归档这段会话？</h3>
        <p id={dialogDescriptionId} className="mt-3 break-words text-sm leading-relaxed text-text-light">
          “{conversation.title || '未知行程'}”将移入“已归档”，之后可以从那里恢复。
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <button type="button" autoFocus onClick={() => dialogRef.current?.close()}
            className="rounded-md border border-black/15 px-4 py-2 text-sm hover:bg-black/5">
            取消
          </button>
          <button type="button" disabled={pending} onClick={handleStatusChange}
            className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-light disabled:opacity-40">
            确认归档
          </button>
        </div>
      </dialog>
    </div>
  );
}

export default memo(ConversationItem);
