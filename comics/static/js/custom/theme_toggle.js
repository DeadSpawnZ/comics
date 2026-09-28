document.addEventListener("DOMContentLoaded", function () {
  const toggleBtn = document.getElementById("theme-toggle");
  const icon = document.getElementById("theme-toggle-icon");

  if (!toggleBtn || !icon) {
    return;
  }

  function applyIcon(theme) {
    icon.textContent = theme === "dark" ? "light_mode" : "dark_mode";
  }

  applyIcon(document.documentElement.getAttribute("data-bs-theme"));

  toggleBtn.addEventListener("click", function () {
    const current = document.documentElement.getAttribute("data-bs-theme");
    const next = current === "dark" ? "light" : "dark";

    document.documentElement.setAttribute("data-bs-theme", next);
    applyIcon(next);

    try {
      localStorage.setItem("theme", next);
    } catch (e) {}
  });
});
