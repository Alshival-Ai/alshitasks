// Apply the saved appearance before painting. Storage is optional (private browsing).
(() => {
  const storageKey = "alshival-hub-theme";
  const normalize = (value) =>
    ["light", "dark", "system"].includes(value) ? value : "system";
  let preference = "system";
  try {
    preference = normalize(localStorage.getItem(storageKey));
  } catch (_) {}
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const apply = () => {
    const dark =
      preference === "dark" || (preference === "system" && media.matches);
    document.documentElement.classList.toggle("dark-style", dark);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
    void window.tasks.theme(preference);
    document.documentElement.dataset.themePreference = preference;
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", dark ? "#000000" : "#f6f4ef");
    document.querySelectorAll("[data-theme-toggle]").forEach((control) => {
      control.checked = dark;
    });
    document.querySelectorAll("[data-theme-auto]").forEach((control) => {
      control.setAttribute("aria-pressed", String(preference === "system"));
    });
    document.querySelectorAll("[data-theme-choice]").forEach((control) => {
      const selected = control.dataset.themeChoice === preference;
      control.setAttribute("aria-checked", String(selected));
      control.tabIndex = selected ? 0 : -1;
    });
  };
  const choose = (value) => {
    preference = normalize(value);
    try {
      localStorage.setItem(storageKey, preference);
    } catch (_) {}
    apply();
  };
  apply();
  media.addEventListener("change", apply);
  window.addEventListener("storage", (event) => {
    if (event.key === storageKey || event.key === null) {
      preference = normalize(event.newValue);
      apply();
    }
  });
  const bindControls = () => {
    document
      .querySelectorAll("[data-theme-toggle]:not([data-theme-bound])")
      .forEach((control) => {
        control.dataset.themeBound = "1";
        control.addEventListener("change", () =>
          choose(control.checked ? "dark" : "light"),
        );
      });
    document
      .querySelectorAll("[data-theme-auto]:not([data-theme-bound])")
      .forEach((control) => {
        control.dataset.themeBound = "1";
        control.addEventListener("click", () => choose("system"));
      });
    // Public pages use three radios; existing portal switches share the preference.
    document
      .querySelectorAll("[data-theme-control]:not([data-theme-bound])")
      .forEach((group) => {
        group.dataset.themeBound = "1";
        const choices = [...group.querySelectorAll("[data-theme-choice]")];
        choices.forEach((control, index) => {
          control.addEventListener("click", () =>
            choose(control.dataset.themeChoice),
          );
          control.addEventListener("keydown", (event) => {
            let next;
            if (event.key === "ArrowRight" || event.key === "ArrowDown")
              next = (index + 1) % choices.length;
            else if (event.key === "ArrowLeft" || event.key === "ArrowUp")
              next = (index + choices.length - 1) % choices.length;
            else if (event.key === "Home") next = 0;
            else if (event.key === "End") next = choices.length - 1;
            else return;
            event.preventDefault();
            choose(choices[next].dataset.themeChoice);
            choices[next].focus();
          });
        });
        group.hidden = false;
      });
    apply();
    document.querySelectorAll("[data-theme-controls]").forEach((control) => {
      control.hidden = false;
    });
  };
  document.addEventListener("DOMContentLoaded", bindControls);
  document.addEventListener("alshival:page", bindControls);
})();
