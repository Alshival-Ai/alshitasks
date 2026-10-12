/* Shared delegated interactions work with both server navigation and React. */
(() => {
  if (window.alshivalSidebarUI) return;
  window.alshivalSidebarUI = true;
  let tooltip, owner;
  const hide = () => { tooltip?.remove(); tooltip = null; owner = null; };
  const show = target => {
    if (!target || document.body.classList.contains('app-menu-open') || matchMedia('(max-width:900px)').matches) return;
    const label = target.getAttribute('aria-label') || target.title;
    if (!label || target === owner) return;
    hide(); owner = target;
    tooltip = document.createElement('div'); tooltip.className = 'wd-sidebar-tooltip'; tooltip.setAttribute('role','tooltip'); tooltip.textContent = label;
    document.body.append(tooltip);
    const rect = target.getBoundingClientRect();
    tooltip.style.left = `${rect.right + 12}px`;
    tooltip.style.top = `${Math.max(8,Math.min(rect.top + (rect.height-tooltip.offsetHeight)/2,innerHeight-tooltip.offsetHeight-8))}px`;
  };
  document.addEventListener('pointerover', e => show(e.target.closest?.('.app-sidebar :is(a,button)')));
  document.addEventListener('pointerout', e => { if (owner && !owner.contains(e.relatedTarget)) hide(); });
  document.addEventListener('focusin', e => show(e.target.closest?.('.app-sidebar :is(a,button)')));
  document.addEventListener('focusout', hide);
  document.addEventListener('scroll', hide, true);
  window.addEventListener('resize', hide);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hide(); });
  document.addEventListener('click', e => {
    hide();
    const button = e.target.closest?.('[data-sidebar-boards]');
    if (button) {
      const menu = document.getElementById(button.dataset.sidebarBoards);
      if (!menu) return;
      const rect = button.getBoundingClientRect();
      menu.style.left = `${Math.min(rect.right+12,innerWidth-254)}px`;
      menu.style.top = `${Math.max(12,Math.min(rect.top,innerHeight-260))}px`;
      menu.togglePopover();
      if (menu.matches(':popover-open')) menu.querySelector('a,button')?.focus();
    } else if (e.target.closest?.('.wd-board-popover :is(a,button)')) {
      e.target.closest('.wd-board-popover').hidePopover();
    }
  });
})();
