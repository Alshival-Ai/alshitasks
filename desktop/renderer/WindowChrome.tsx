import { useEffect, useState } from "react";
import "./theme-selector.css";
export function WindowChrome() {
  const [maximized, setMaximized] = useState(false);
  useEffect(() => {
    document.dispatchEvent(new Event("alshival:page"));
    const off = window.tasks.onWindowState(setMaximized);
    void window.tasks.windowAction("state").then(setMaximized);
    return off;
  }, []);
  return (
    <div className="window-chrome app-header-actions">
      <div
        aria-label="Color theme"
        className="theme-control"
        data-theme-control=""
        role="radiogroup"
      >
        <button
          aria-checked="false"
          aria-label="Light"
          data-theme-choice="light"
          role="radio"
          tabIndex={-1}
          title="Light mode"
          type="button"
        >
          <svg
            aria-hidden="true"
            fill="none"
            focusable="false"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1.7"
            viewBox="0 0 24 24"
          >
            <circle cx="12" cy="12" r="4"></circle>
            <path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"></path>
          </svg>
        </button>
        <button
          aria-checked="false"
          aria-label="Dark"
          data-theme-choice="dark"
          role="radio"
          tabIndex={-1}
          title="Dark mode"
          type="button"
        >
          <svg
            aria-hidden="true"
            fill="none"
            focusable="false"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1.7"
            viewBox="0 0 24 24"
          >
            <path d="M20.5 13A8.5 8.5 0 0 1 11 3.5 8.5 8.5 0 1 0 20.5 13Z"></path>
          </svg>
        </button>
        <button
          aria-checked="true"
          aria-label="System"
          data-theme-choice="system"
          role="radio"
          tabIndex={0}
          title="System — follow device appearance"
          type="button"
        >
          <svg
            aria-hidden="true"
            fill="none"
            focusable="false"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1.7"
            viewBox="0 0 24 24"
          >
            <rect height="13" rx="2" width="18" x="3" y="4"></rect>
            <path d="M8 21h8m-4-4v4"></path>
          </svg>
        </button>
      </div>

      <div className="window-controls" aria-label="Window controls">
        <button
          aria-label="Minimize window"
          title="Minimize"
          onClick={() => void window.tasks.windowAction("minimize")}
        >
          <svg viewBox="0 0 16 16">
            <path d="M3 8h10" />
          </svg>
        </button>
        <button
          aria-label={maximized ? "Restore window" : "Maximize window"}
          title={maximized ? "Restore" : "Maximize"}
          onClick={() => void window.tasks.windowAction("maximize")}
        >
          <svg viewBox="0 0 16 16">
            {maximized ? (
              <path d="M5 5V3h8v8h-2M3 5h8v8H3Z" />
            ) : (
              <path d="M3 3h10v10H3Z" />
            )}
          </svg>
        </button>
        <button
          className="window-close"
          aria-label="Close window"
          title="Close"
          onClick={() => void window.tasks.windowAction("close")}
        >
          <svg viewBox="0 0 16 16">
            <path d="m4 4 8 8m0-8-8 8" />
          </svg>
        </button>
      </div>
    </div>
  );
}
