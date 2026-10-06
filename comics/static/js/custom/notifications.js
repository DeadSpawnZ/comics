// Navbar bell: loads the latest notifications when its menu opens, so a page only pays for the
// unread badge. The menu is an HTML fragment rendered by Django (every user or admin text in it
// is escaped by the template engine); it is parsed with DOMParser and its nodes moved in, and on
// failure only a fixed text is shown (textContent).
document.addEventListener("DOMContentLoaded", function () {
  const root = document.querySelector("[data-notifications]");
  if (!root) {
    return;
  }
  const body = root.querySelector("[data-notifications-body]");
  const status = body.querySelector("[data-notifications-status]");
  const errorText = status ? status.dataset.errorText : "";

  function showError() {
    const message = document.createElement("p");
    message.className = "notif-menu__status";
    message.textContent = errorText;
    body.replaceChildren(message);
  }

  root.addEventListener("show.bs.dropdown", function () {
    // "next": where "Mark all as read" brings the user back (validated by the server).
    const url = root.dataset.menuUrl + "?next=" + encodeURIComponent(window.location.pathname + window.location.search);
    body.setAttribute("aria-busy", "true");
    fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (response) {
        if (!response.ok || response.redirected) {
          throw new Error("HTTP " + response.status);
        }
        return response.text();
      })
      .then(function (html) {
        const fragment = new DOMParser().parseFromString(html, "text/html");
        body.replaceChildren(...fragment.body.childNodes);
      })
      .catch(showError)
      .finally(function () {
        body.removeAttribute("aria-busy");
      });
  });
});
