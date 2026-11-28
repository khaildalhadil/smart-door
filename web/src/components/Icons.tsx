/** One SVG sprite, drawn with the current text colour, and <Icon name="..."/>. */
export function IconSprite() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
      <defs>
        <g id="i-door"><path d="M6 21V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v17" /><path d="M3 21h18" /><path d="M14.5 12.4v.2" /></g>
        <g id="i-ban"><circle cx="12" cy="12" r="9" /><path d="M5.6 5.6l12.8 12.8" /></g>
        <g id="i-bell"><path d="M6 16v-5a6 6 0 1 1 12 0v5l1.5 2h-15z" /><path d="M10 20.5a2 2 0 0 0 4 0" /></g>
        <g id="i-flame"><path d="M12 2.5c.8 3.2 4.5 5 4.5 9.5a4.5 4.5 0 0 1-9 0c0-1.7.7-3 1.8-4 .3 1.6 1 2.3 1.7 2.3 0-2.8 1-5.3 1-7.8z" transform="translate(0 2.5)" /></g>
        <g id="i-clock"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></g>
        <g id="i-user"><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></g>
        <g id="i-camera"><path d="M4 8h3l1.5-2h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z" /><circle cx="12" cy="13" r="3.5" /></g>
        <g id="i-refresh"><path d="M20 11a8 8 0 1 0-2.3 6" /><path d="M20 4v7h-7" /></g>
        <g id="i-lock"><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></g>
        <g id="i-chat"><path d="M21 12a8 8 0 0 1-11.8 7L4 20l1.1-4.2A8 8 0 1 1 21 12z" /></g>
        <g id="i-up"><path d="M12 19V5M6 11l6-6 6 6" /></g>
        <g id="i-down"><path d="M12 5v14M6 13l6 6 6-6" /></g>
        <g id="i-logout"><path d="M10 4H6a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h4" /><path d="M15 8l4 4-4 4M19 12H9" /></g>
        <g id="i-theme"><circle cx="12" cy="12" r="9" /><path d="M12 3v18a9 9 0 0 0 0-18z" fill="currentColor" /></g>
        <g id="i-key"><circle cx="8" cy="15" r="4" /><path d="M11 12l8-8M16 7l2 2" /></g>
        <g id="i-gear"><circle cx="12" cy="12" r="3" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" /></g>
        <g id="i-check"><path d="M5 12.5l4.5 4.5L19 7.5" /></g>
        <g id="i-alert"><path d="M12 4l9 16H3z" /><path d="M12 10v4M12 17.1v.2" /></g>
        <g id="i-close"><path d="M6 6l12 12M18 6L6 18" /></g>
        <g id="i-prev"><path d="M9 6l6 6-6 6" /></g>
        <g id="i-next"><path d="M15 6l-6 6 6 6" /></g>
        <g id="i-table"><rect x="4" y="5" width="16" height="14" rx="2" /><path d="M4 10h16M10 5v14" /></g>
        <g id="i-shield"><path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z" /><path d="M9 12l2 2 4-4" /></g>
        <g id="i-plus"><path d="M12 5v14M5 12h14" /></g>
        <g id="i-trash"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></g>
        <g id="i-card"><rect x="3" y="6" width="18" height="12" rx="2" /><path d="M7 14h4" /><path d="M16 9.5a3 3 0 0 1 0 4M18.2 8a5.5 5.5 0 0 1 0 7" /></g>
        <g id="i-globe"><circle cx="12" cy="12" r="9" /><path d="M3 12h18" /><path d="M12 3c2.5 2.7 3.7 5.7 3.7 9s-1.2 6.3-3.7 9c-2.5-2.7-3.7-5.7-3.7-9s1.2-6.3 3.7-9z" /></g>
        <g id="i-nophoto"><path d="M4 8h3l1.5-2h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z" /><circle cx="12" cy="13" r="3.5" /><path d="M3.5 3.5l17 17" /></g>
        <g id="i-send"><path d="M4 12l16-8-6 16-2.5-6.5z" /><path d="M11.5 13.5L20 4" /></g>
        <g id="i-photo"><rect x="3" y="5" width="18" height="14" rx="2" /><circle cx="9" cy="10" r="2" /><path d="M21 16l-5-5-8 8" /></g>
      </defs>
    </svg>
  );
}

export function Icon({ name, className }: { name: string; className?: string }) {
  return (
    <svg className={"icon" + (className ? " " + className : "")} viewBox="0 0 24 24" aria-hidden="true">
      <use href={"#i-" + name} />
    </svg>
  );
}
