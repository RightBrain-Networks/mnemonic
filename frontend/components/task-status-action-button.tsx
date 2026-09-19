"use client";

import { createPortal } from "react-dom";
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import type { Project } from "@/lib/types";
import type { ManualStatusAction } from "@/lib/work-status-actions";

export type TaskStatusAction = { value: ManualStatusAction; label: string; disabledReason?: string | null };

export default function TaskStatusActionButton({
  subject: work, subjectLabel = "work item", actions, deferred, projects = [],
  disabled, busy, moveDisabled = true, moving = false, moveTitle = "",
  moveExplanationId, onAction, onMove, compact = false
}: {
  subject: { id: string; project_id: string; title: string };
  subjectLabel?: string;
  actions: readonly TaskStatusAction[];
  deferred: boolean;
  projects?: readonly Project[];
  disabled: boolean;
  busy: boolean;
  moveDisabled?: boolean;
  moving?: boolean;
  moveTitle?: string;
  moveExplanationId?: string;
  onAction: (action: ManualStatusAction) => void;
  onMove?: (targetProjectId: string) => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [moveMenuStyle, setMoveMenuStyle] = useState<CSSProperties>({
    position: "fixed",
    top: 0,
    left: 0,
    right: "auto",
    visibility: "hidden"
  });
  const [compactMenuStyle, setCompactMenuStyle] = useState<CSSProperties>({
    position: "fixed",
    top: 0,
    left: 0,
    right: "auto",
    bottom: "auto",
    visibility: "hidden"
  });
  const rootRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const moveRootRef = useRef<HTMLDivElement>(null);
  const moveItemRef = useRef<HTMLButtonElement>(null);
  const moveMenuRef = useRef<HTMLDivElement>(null);
  const moveCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const moveBlurFrame = useRef<number | null>(null);
  const restoreMoveFocusFrame = useRef<number | null>(null);
  const pointerWithinMove = useRef(false);
  const suppressMoveFocusOpen = useRef(false);
  const moveTargetFocused = useRef(false);
  const openingFocus = useRef<"first" | "last">("first");
  const menuId = useId();
  const moveItemId = useId();
  const moveMenuId = useId();
  const targetProjects = projects.filter((project) => project.id !== work.project_id);
  const targetProjectKey = targetProjects.map((project) => project.id).join(":");
  const moveLayoutKey = JSON.stringify({
    actions: actions.map((action) => action.value),
    projects: targetProjects.map((project) => [project.id, project.name, project.slug])
  });
  const moveUnavailable = moveDisabled || moving || targetProjects.length === 0;
  const controlsBusy = busy || moving;
  const primaryDisabled = disabled || controlsBusy || deferred;

  useEffect(() => {
    setOpen(false);
    closeMoveMenu();
    cancelRestoreMoveFocus();
    suppressMoveFocusOpen.current = false;
  }, [work.id, work.project_id]);
  useEffect(() => {
    if (disabled || controlsBusy) {
      setOpen(false);
      closeMoveMenu();
      cancelRestoreMoveFocus();
      suppressMoveFocusOpen.current = false;
    }
  }, [controlsBusy, disabled]);
  useEffect(() => {
    const restoreMoveFocus = moveTargetFocused.current;
    if (!moveUnavailable && !restoreMoveFocus) return;
    closeMoveMenu();
    cancelRestoreMoveFocus();
    if (restoreMoveFocus) {
      const trigger = moveItemRef.current;
      restoreMoveFocusFrame.current = requestAnimationFrame(() => {
        restoreMoveFocusFrame.current = null;
        if (!trigger?.isConnected || moveItemRef.current !== trigger) {
          suppressMoveFocusOpen.current = false;
          return;
        }
        suppressMoveFocusOpen.current = true;
        trigger.focus();
        suppressMoveFocusOpen.current = false;
      });
    }
    return cancelRestoreMoveFocus;
  }, [moveUnavailable, targetProjectKey]);
  useEffect(() => {
    if (!open) {
      closeMoveMenu();
      cancelRestoreMoveFocus();
      suppressMoveFocusOpen.current = false;
      return;
    }
    const closeCascade = () => {
      setOpen(false);
      closeMoveMenu();
      cancelRestoreMoveFocus();
      suppressMoveFocusOpen.current = false;
    };
    const closeOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (
        !rootRef.current?.contains(target)
        && !moveMenuRef.current?.contains(target)
      ) closeCascade();
    };
    document.addEventListener("pointerdown", closeOutside);
    window.addEventListener("blur", closeCascade);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      window.removeEventListener("blur", closeCascade);
    };
  }, [open]);
  useLayoutEffect(() => {
    if (!open || !compact) return;
    const trigger = rootRef.current;
    const menu = menuRef.current;
    if (!trigger || !menu) return;
    const positionMenu = (event?: Event) => {
      if (event?.target instanceof Node && menu.contains(event.target)) return;
      const triggerRect = trigger.getBoundingClientRect();
      const scrollPane = trigger.closest<HTMLElement>(".work-queue-list");
      const paneRect = scrollPane?.getBoundingClientRect();
      const visibleTop = Math.max(0, paneRect?.top ?? 0);
      const visibleRight = Math.min(window.innerWidth, paneRect?.right ?? window.innerWidth);
      const visibleBottom = Math.min(window.innerHeight, paneRect?.bottom ?? window.innerHeight);
      const visibleLeft = Math.max(0, paneRect?.left ?? 0);
      if (
        triggerRect.bottom <= visibleTop
        || triggerRect.left >= visibleRight
        || triggerRect.top >= visibleBottom
        || triggerRect.right <= visibleLeft
      ) {
        setOpen(false);
        return;
      }
      const viewportMargin = 16;
      const menuGap = 6;
      const availableWidth = Math.max(155, window.innerWidth - viewportMargin * 2);
      const width = Math.min(Math.max(155, menu.scrollWidth), availableWidth);
      const maxHeight = Math.min(320, Math.max(80, window.innerHeight - viewportMargin * 2));
      const height = Math.min(menu.scrollHeight, maxHeight);
      const roomBelow = window.innerHeight - triggerRect.bottom - viewportMargin - menuGap;
      const roomAbove = triggerRect.top - viewportMargin - menuGap;
      const opensDown = roomBelow >= height || roomBelow >= roomAbove;
      const top = opensDown
        ? Math.min(triggerRect.bottom + menuGap, window.innerHeight - height - viewportMargin)
        : Math.max(viewportMargin, triggerRect.top - height - menuGap);
      const left = Math.min(
        Math.max(viewportMargin, triggerRect.left),
        Math.max(viewportMargin, window.innerWidth - width - viewportMargin)
      );
      setCompactMenuStyle((current) => (
        current.top === top
        && current.left === left
        && current.width === width
        && current.maxHeight === maxHeight
        && current.visibility === "visible"
          ? current
          : {
            position: "fixed",
            top,
            left,
            right: "auto",
            bottom: "auto",
            width,
            maxHeight,
            overflowY: "auto",
            visibility: "visible"
          }
      ));
    };
    positionMenu();
    window.addEventListener("resize", positionMenu);
    document.addEventListener("scroll", positionMenu, true);
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => positionMenu());
    resizeObserver?.observe(trigger);
    resizeObserver?.observe(menu);
    return () => {
      window.removeEventListener("resize", positionMenu);
      document.removeEventListener("scroll", positionMenu, true);
      resizeObserver?.disconnect();
    };
  }, [compact, moveLayoutKey, open]);
  useEffect(() => {
    // Compact menus are hidden until placement finishes; hidden items cannot take focus.
    if (!open || compact && compactMenuStyle.visibility !== "visible") return;
    const items = mainMenuItems();
    const target = openingFocus.current === "last" ? items.at(-1) : items[0];
    target?.focus();
  }, [compact, compactMenuStyle.visibility, open]);
  useEffect(() => () => {
    cancelMoveClose();
    cancelMoveBlur();
    cancelRestoreMoveFocus();
  }, []);
  useLayoutEffect(() => {
    if (!moveOpen) return;
    const trigger = moveItemRef.current;
    const submenu = moveMenuRef.current;
    if (!trigger || !submenu) return;
    const positionMenu = (event?: Event) => {
      if (event?.target instanceof Node && submenu.contains(event.target)) return;
      const triggerRect = trigger.getBoundingClientRect();
      const scrollPane = trigger.closest<HTMLElement>(".detail-scroll");
      const paneRect = scrollPane?.getBoundingClientRect();
      const visibleTop = Math.max(0, paneRect?.top ?? 0);
      const visibleRight = Math.min(window.innerWidth, paneRect?.right ?? window.innerWidth);
      const visibleBottom = Math.min(window.innerHeight, paneRect?.bottom ?? window.innerHeight);
      const visibleLeft = Math.max(0, paneRect?.left ?? 0);
      if (
        triggerRect.bottom <= visibleTop
        || triggerRect.left >= visibleRight
        || triggerRect.top >= visibleBottom
        || triggerRect.right <= visibleLeft
      ) {
        closeMoveMenu();
        return;
      }
      const availableWidth = Math.max(160, window.innerWidth - 32);
      // Keep the flyout width independent from its currently rendered width.
      // Measuring scrollWidth while also observing the submenu creates a resize
      // feedback loop on narrow viewports, which makes the menu move under the
      // pointer and can close it before a project is selected.
      const width = Math.min(320, availableWidth);
      const maxHeight = Math.min(320, Math.max(120, window.innerHeight - 32));
      const height = Math.min(submenu.scrollHeight, maxHeight);
      const opensRight = window.innerWidth - triggerRect.right - 16 >= width;
      const left = opensRight
        ? triggerRect.right - 1
        : Math.max(16, triggerRect.left - width + 1);
      const top = Math.min(
        Math.max(16, triggerRect.top - 5),
        Math.max(16, window.innerHeight - height - 16)
      );
      setMoveMenuStyle((current) => (
        current.top === top
        && current.left === left
        && current.width === width
        && current.maxHeight === maxHeight
        && current.visibility === "visible"
          ? current
          : {
            position: "fixed",
            top,
            left,
            right: "auto",
            width,
            maxHeight,
            visibility: "visible"
          }
      ));
    };
    positionMenu();
    window.addEventListener("resize", positionMenu);
    document.addEventListener("scroll", positionMenu, true);
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => positionMenu());
    if (resizeObserver) {
      resizeObserver.observe(trigger);
      if (menuRef.current) resizeObserver.observe(menuRef.current);
    }
    return () => {
      window.removeEventListener("resize", positionMenu);
      document.removeEventListener("scroll", positionMenu, true);
      resizeObserver?.disconnect();
    };
  }, [moveLayoutKey, moveOpen]);

  function mainMenuItems(): HTMLButtonElement[] {
    return [...(menuRef.current?.querySelectorAll<HTMLButtonElement>(
      `[data-status-menu-item="true"]`
    ) ?? [])].filter((item) => !item.disabled);
  }

  function moveMenuItems(): HTMLButtonElement[] {
    return [...(moveMenuRef.current?.querySelectorAll<HTMLButtonElement>(
      ":scope > button:not(:disabled)"
    ) ?? [])];
  }

  function cancelMoveClose(): void {
    if (moveCloseTimer.current !== null) {
      clearTimeout(moveCloseTimer.current);
      moveCloseTimer.current = null;
    }
  }

  function cancelMoveBlur(): void {
    if (moveBlurFrame.current !== null) {
      cancelAnimationFrame(moveBlurFrame.current);
      moveBlurFrame.current = null;
    }
  }

  function cancelRestoreMoveFocus(): void {
    if (restoreMoveFocusFrame.current !== null) {
      cancelAnimationFrame(restoreMoveFocusFrame.current);
      restoreMoveFocusFrame.current = null;
    }
  }

  function closeMoveMenu(): void {
    cancelMoveClose();
    cancelMoveBlur();
    pointerWithinMove.current = false;
    moveTargetFocused.current = false;
    setMoveOpen(false);
  }

  function checkMoveBlur(): void {
    cancelMoveBlur();
    moveBlurFrame.current = requestAnimationFrame(() => {
      moveBlurFrame.current = null;
      const active = document.activeElement;
      const focusInRoot = moveRootRef.current?.contains(active) ?? false;
      const focusInMenu = moveMenuRef.current?.contains(active) ?? false;
      moveTargetFocused.current = focusInMenu;
      if (
        !focusInRoot
        && !focusInMenu
        && !pointerWithinMove.current
      ) closeMoveMenu();
    });
  }

  function scheduleMoveClose(): void {
    cancelMoveClose();
    moveCloseTimer.current = setTimeout(() => {
      moveCloseTimer.current = null;
      const active = document.activeElement;
      if (
        !moveRootRef.current?.contains(active)
        && !moveMenuRef.current?.contains(active)
      ) closeMoveMenu();
    }, 180);
  }

  function openMoveAndFocus(position: "first" | "last" = "first"): void {
    if (moveUnavailable) return;
    cancelMoveClose();
    setMoveOpen(true);
    requestAnimationFrame(() => {
      const items = moveMenuItems();
      (position === "last" ? items.at(-1) : items[0])?.focus();
    });
  }

  function closeAndFocus(): void {
    setOpen(false);
    closeMoveMenu();
    toggleRef.current?.focus();
  }

  function leaveCascadeWithTab(backwards: boolean): void {
    const chooser = toggleRef.current;
    const root = rootRef.current;
    if (!chooser || !root) {
      setOpen(false);
      closeMoveMenu();
      return;
    }
    const focusable = [...document.querySelectorAll<HTMLElement>(
      "button:not(:disabled):not([tabindex=\"-1\"]), a[href], input:not(:disabled), "
      + "select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex=\"-1\"])"
    )].filter((element) => element.getClientRects().length > 0);
    const chooserIndex = focusable.indexOf(chooser);
    const destination = backwards
      ? focusable.slice(0, chooserIndex).at(-1)
      : focusable.slice(chooserIndex + 1).find((element) => !root.contains(element));
    setOpen(false);
    closeMoveMenu();
    destination?.focus();
  }

  function moveMenuFocus(event: KeyboardEvent<HTMLDivElement>): void {
    if (moveMenuRef.current?.contains(event.target as Node)) return;
    const items = mainMenuItems();
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    let next: number | null = null;
    if (event.key === "ArrowDown") next = current < items.length - 1 ? current + 1 : 0;
    if (event.key === "ArrowUp") next = current > 0 ? current - 1 : items.length - 1;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = items.length - 1;
    if (next !== null) {
      event.preventDefault();
      event.stopPropagation();
      items[next]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeAndFocus();
    } else if (event.key === "Tab") {
      event.preventDefault();
      event.stopPropagation();
      leaveCascadeWithTab(event.shiftKey);
    }
  }

  function moveProjectFocus(event: KeyboardEvent<HTMLDivElement>): void {
    const items = moveMenuItems();
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    let next: number | null = null;
    if (event.key === "ArrowDown") next = current < items.length - 1 ? current + 1 : 0;
    if (event.key === "ArrowUp") next = current > 0 ? current - 1 : items.length - 1;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = items.length - 1;
    if (next !== null) {
      event.preventDefault();
      event.stopPropagation();
      items[next]?.focus();
    } else if (event.key === "ArrowLeft" || event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeMoveMenu();
      suppressMoveFocusOpen.current = true;
      moveItemRef.current?.focus();
      suppressMoveFocusOpen.current = false;
    } else if (event.key === "Tab") {
      event.preventDefault();
      event.stopPropagation();
      leaveCascadeWithTab(event.shiftKey);
    }
  }

  return <div
    className={`status-split-button ${compact ? "queue-status-split-button" : ""}`}
    ref={rootRef}
    onClick={(event) => event.stopPropagation()}
  >
    <button
      className="button defer-button status-split-primary"
      type="button"
      disabled={primaryDisabled}
      aria-label={`Defer ${work.title}`}
      title={deferred
        ? `This ${subjectLabel} is already Deferred. Choose another status from the menu.`
        : `Explicitly hold this ${subjectLabel} out of its queue`}
      onClick={() => onAction("defer")}
    >{moving ? "Moving…" : busy ? "Saving…" : "Defer"}</button>
    <button
      ref={toggleRef}
      className="button defer-button status-split-toggle"
      type="button"
      disabled={disabled || controlsBusy}
      aria-label={`Choose an action for ${work.title}`}
      aria-haspopup="menu"
      aria-expanded={open}
      aria-controls={menuId}
      onClick={() => {
        openingFocus.current = "first";
        setOpen((value) => !value);
      }}
      onKeyDown={(event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          event.stopPropagation();
          openingFocus.current = event.key === "ArrowUp" ? "last" : "first";
          setOpen(true);
        }
      }}
    ><span aria-hidden="true">⌄</span></button>
    {open && <div
      ref={menuRef}
      className="status-action-menu"
      style={compact ? compactMenuStyle : undefined}
      id={menuId}
      role="menu"
      aria-label={`Actions for ${work.title}`}
      aria-owns={moveOpen ? moveMenuId : undefined}
      onKeyDown={moveMenuFocus}
    >{actions.map((action) => {
      const reason = action.disabledReason;
      return <button
        type="button"
        role="menuitem"
        tabIndex={-1}
        key={action.value}
        data-status-menu-item="true"
        disabled={Boolean(reason)}
        title={reason ?? `Explicitly mark this ${subjectLabel} ${action.label}`}
        aria-label={`${action.label} ${work.title}`}
        onFocus={closeMoveMenu}
        onClick={() => {
          setOpen(false);
          closeMoveMenu();
          onAction(action.value);
        }}
      >{action.label}</button>;
    })}
    {onMove && <><div role="separator" className="status-action-separator" />
    <div
      ref={moveRootRef}
      className="status-move-menu-item"
      role="none"
      onPointerEnter={() => {
        pointerWithinMove.current = true;
        cancelMoveClose();
        if (!moveUnavailable) setMoveOpen(true);
      }}
      onPointerLeave={() => {
        pointerWithinMove.current = false;
        scheduleMoveClose();
      }}
      onBlur={checkMoveBlur}
    >
      <button
        ref={moveItemRef}
        id={moveItemId}
        type="button"
        role="menuitem"
        tabIndex={-1}
        data-status-menu-item="true"
        aria-label={`Move ${work.title} to another project`}
        aria-haspopup="menu"
        aria-expanded={moveOpen}
        aria-controls={moveMenuId}
        aria-disabled={moveUnavailable}
        aria-describedby={moveExplanationId}
        title={moveTitle}
        onFocus={() => {
          cancelMoveClose();
          if (suppressMoveFocusOpen.current) {
            suppressMoveFocusOpen.current = false;
          } else if (!moveUnavailable) {
            setMoveOpen(true);
          }
        }}
        onClick={(event) => {
          if (moveUnavailable) {
            event.preventDefault();
            return;
          }
          openMoveAndFocus();
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowRight" || event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            event.stopPropagation();
            openMoveAndFocus();
          }
        }}
      ><span>{moving ? "Moving…" : "Move"}</span><span aria-hidden="true">›</span></button>
      {moveOpen && createPortal(<div
        ref={moveMenuRef}
        style={moveMenuStyle}
        className="status-action-menu status-action-submenu move-project-menu"
        id={moveMenuId}
        role="menu"
        aria-label={`Move ${work.title} to project`}
        onPointerDownCapture={() => {
          pointerWithinMove.current = true;
        }}
        onPointerEnter={() => {
          pointerWithinMove.current = true;
          cancelMoveClose();
        }}
        onPointerLeave={() => {
          pointerWithinMove.current = false;
          scheduleMoveClose();
        }}
        onKeyDown={moveProjectFocus}
      >{targetProjects.map((project) => <button
        type="button"
        role="menuitem"
        tabIndex={-1}
        key={project.id}
        aria-label={`${project.name} (${project.slug})`}
        onFocus={() => {
          moveTargetFocused.current = true;
        }}
        onBlur={checkMoveBlur}
        onClick={() => {
          if (moveUnavailable) return;
          closeMoveMenu();
          setOpen(false);
          onMove(project.id);
        }}
      ><span className="move-project-identity"><bdi dir="auto">{project.name}</bdi>
          <small>{project.slug}</small></span></button>)}</div>, document.body)}
    </div></>}
    </div>}
  </div>;
}
