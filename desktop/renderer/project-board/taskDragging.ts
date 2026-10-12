import { lifecycle, type Item, type Status } from "./types";

type Responsive = ReturnType<Window["AlshivalResponsiveBoard"]>;
/** Match the portal's pointer/long-press gestures without native HTML dragging. */
export function taskDragging(
  root: HTMLElement,
  life: ReturnType<typeof lifecycle>,
  responsive: Responsive,
  current: () => { tasks: Item[]; statuses: Status[]; busy: boolean },
  move: (item: Item, status: string) => void,
  report: (message: string) => void,
) {
  type Hold = {
    item: Item;
    card: HTMLElement;
    x: number;
    y: number;
    touch: boolean;
  };
  let hold: Hold | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let drag:
    (Hold & { ghost: HTMLElement; layer: HTMLElement; frame: number }) | null =
    null;
  let destination: { target: HTMLElement; state: string } | null = null;
  let suppressUntil = 0;
  const clearHold = () => {
    clearTimeout(timer);
    hold = null;
  };
  const update = (x: number, y: number) => {
    if (!drag) return;
    drag.x = x;
    drag.y = y;
    const bounds = drag.ghost.getBoundingClientRect();
    drag.ghost.style.left = `${Math.max(12, Math.min(x + 12, innerWidth - bounds.width - 12))}px`;
    drag.ghost.style.top = `${Math.max(12, Math.min(y + 12, innerHeight - bounds.height - 12))}px`;
    const states = current().statuses.map((s) => s.id);
    let next = responsive?.dragTarget(x, y, states);
    if (next === undefined) {
      const target = document
        .elementFromPoint(x, y)
        ?.closest<HTMLElement>("[data-sp-state]");
      next =
        target &&
        root.contains(target) &&
        states.includes(target.dataset.spState ?? "")
          ? { target, state: target.dataset.spState! }
          : null;
    }
    destination?.target.classList.remove("sp-drop-active");
    if (next?.state !== destination?.state) {
      const status = current().statuses.find((s) => s.id === next?.state);
      report(
        status
          ? `Release to move to ${status.name}.`
          : "Release outside the board to cancel.",
      );
    }
    destination = next ?? null;
    destination?.target.classList.add("sp-drop-active");
  };
  const tick = () => {
    if (!drag) return;
    responsive?.dragTick(drag.x, drag.y, performance.now());
    update(drag.x, drag.y);
    drag.frame = requestAnimationFrame(tick);
  };
  const finish = (cancel = false) => {
    clearHold();
    responsive?.resetTouch();
    if (!drag) return;
    const active = drag,
      target = destination;
    cancelAnimationFrame(active.frame);
    active.layer.remove();
    active.card.classList.remove("sp-dragging");
    root.classList.remove("sp-is-dragging");
    destination?.target.classList.remove("sp-drop-active");
    destination = null;
    drag = null;
    responsive?.finishDrag();
    suppressUntil = performance.now() + 400;
    report("");
    if (!cancel && target) move(active.item, target.state);
  };
  const begin = (value: Hold) => {
    clearHold();
    if (drag || current().busy) return;
    window.getSelection()?.removeAllRanges();
    const layer = document.createElement("div");
    layer.className = "shared-project sp-drag-layer";
    const ghost = value.card.cloneNode(true) as HTMLElement;
    ghost.classList.add("sp-drag-ghost");
    ghost.setAttribute("aria-hidden", "true");
    ghost.inert = true;
    ghost.removeAttribute("data-project-card");
    ghost
      .querySelectorAll("[id]")
      .forEach((node) => node.removeAttribute("id"));
    ghost.style.width = `${Math.min(value.card.getBoundingClientRect().width, 280)}px`;
    layer.append(ghost);
    document.body.append(layer);
    value.card.classList.add("sp-dragging");
    root.classList.add("sp-is-dragging");
    drag = { ...value, ghost, layer, frame: 0 };
    responsive?.beginDrag();
    update(value.x, value.y);
    tick();
  };
  const candidate = (
    target: EventTarget | null,
    x: number,
    y: number,
    touch: boolean,
  ): Hold | null => {
    if (
      current().busy ||
      !(target instanceof Element) ||
      target.closest("button,input,select,textarea,summary,a,[popover]")
    )
      return null;
    const card = target.closest<HTMLElement>("[data-project-card]");
    const item = current().tasks.find(
      (item) => item.id === card?.dataset.projectCard,
    );
    return card && item && !item.data.archived
      ? { card, item, x, y, touch }
      : null;
  };
  const listen = <K extends keyof GlobalEventHandlersEventMap>(
    target: EventTarget,
    name: K,
    fn: (event: GlobalEventHandlersEventMap[K]) => void,
    options?: AddEventListenerOptions,
  ) => life.listen(target, name, fn as EventListener, options);
  listen(root, "dragstart", (event) => {
    if ((event.target as Element).closest("[data-project-card]"))
      event.preventDefault();
  });
  listen(root, "selectstart", (event) => {
    if (hold || drag) event.preventDefault();
  });
  listen(root, "pointerdown", (event) => {
    if (
      event.pointerType === "touch" ||
      event.button !== 0 ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    hold = candidate(event.target, event.clientX, event.clientY, false);
    if (hold) event.preventDefault();
  });
  listen(window, "pointermove", (event) => {
    if (event.pointerType === "touch") return;
    if (hold && Math.hypot(event.clientX - hold.x, event.clientY - hold.y) > 6)
      begin(hold);
    if (drag && !drag.touch) {
      event.preventDefault();
      update(event.clientX, event.clientY);
    }
  });
  listen(window, "pointerup", (event) => {
    if (event.pointerType === "touch") return;
    if (drag) update(event.clientX, event.clientY);
    finish();
  });
  listen(window, "pointercancel", (event) => {
    if (event.pointerType !== "touch") finish(true);
  });
  listen(
    root,
    "touchstart",
    (event) => {
      if (event.touches.length !== 1) {
        finish(true);
        return;
      }
      responsive?.touchStart(event);
      const touch = event.touches[0];
      hold = candidate(event.target, touch.clientX, touch.clientY, true);
      if (hold)
        timer = setTimeout(() => {
          if (hold) begin(hold);
        }, 350);
    },
    { passive: true },
  );
  listen(
    root,
    "touchmove",
    (event) => {
      if (event.touches.length !== 1) {
        finish(true);
        return;
      }
      const touch = event.touches[0];
      if (drag) {
        if (!event.cancelable) {
          finish(true);
          return;
        }
        event.preventDefault();
        update(touch.clientX, touch.clientY);
      } else {
        if (responsive?.touchMove(event)) clearHold();
        if (
          hold &&
          Math.hypot(touch.clientX - hold.x, touch.clientY - hold.y) > 8
        )
          clearHold();
      }
    },
    { passive: false },
  );
  listen(
    root,
    "touchend",
    (event) => {
      const touch = event.changedTouches[0];
      if (drag && touch) update(touch.clientX, touch.clientY);
      if (!drag && responsive?.touchEnd()) {
        if (event.cancelable) event.preventDefault();
        suppressUntil = performance.now() + 400;
      }
      finish();
    },
    { passive: false },
  );
  listen(root, "touchcancel", () => finish(true));
  listen(
    root,
    "click",
    (event) => {
      if (performance.now() < suppressUntil) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    { capture: true },
  );
  listen(root, "contextmenu", (event) => {
    if (hold || drag) event.preventDefault();
  });
  listen(window, "keydown", (event) => {
    if (event.key === "Escape" && (hold || drag)) {
      event.preventDefault();
      finish(true);
    }
  });
  listen(window, "blur", () => finish(true));
  life.onClose(() => finish(true));
  return { cancel: () => finish(true) };
}
