/*
 * Achievement categories on a profile: each category medallion is a disclosure button for its
 * panel of challenges. One panel is open at a time; it is moved (never rebuilt: the server
 * rendered and escaped it) to right after the last button of the clicked button's row, so it
 * spans the whole width below that row. On a one-column layout that is directly after the button.
 * The buttons keep the focus and aria-expanded tells their state.
 *
 * Rows are worked out from the grid's column count (not from measured positions), so an open
 * panel never has to be hidden to measure. On resize the panel only moves when its row changes,
 * once per frame, and its fade-in only plays when it opens.
 */
(function () {
  "use strict";

  document.querySelectorAll("[data-trophy-grid]").forEach(function (grid) {
    const buttons = Array.from(grid.querySelectorAll("button[aria-controls]"));
    let open = null;
    let frame = 0;

    function columns() {
      const tracks = getComputedStyle(grid).gridTemplateColumns;
      return tracks && tracks !== "none" ? tracks.trim().split(/\s+/).length : 1;
    }

    // The last button on the same row as `button`.
    function lastOfRow(button) {
      const perRow = columns();
      const index = buttons.indexOf(button);
      const last = Math.min((Math.floor(index / perRow) + 1) * perRow, buttons.length) - 1;
      return buttons[last];
    }

    function place(button, panel) {
      const target = lastOfRow(button);
      if (panel.previousElementSibling !== target) {
        target.after(panel);
      }
    }

    function close() {
      if (!open) {
        return;
      }
      open.button.setAttribute("aria-expanded", "false");
      open.panel.hidden = true;
      open.panel.classList.remove("is-settled");
      open = null;
    }

    function show(button) {
      const panel = document.getElementById(button.getAttribute("aria-controls"));
      if (!panel) {
        return;
      }
      close();
      place(button, panel);
      panel.hidden = false;
      button.setAttribute("aria-expanded", "true");
      open = { button: button, panel: panel };
    }

    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        if (open && open.button === button) {
          close();
        } else {
          show(button);
        }
      });
    });

    // Once shown, moving the panel to another row must not replay its fade-in.
    grid.addEventListener("animationend", function (event) {
      if (event.target.classList.contains("trophy-panel")) {
        event.target.classList.add("is-settled");
      }
    });

    window.addEventListener("resize", function () {
      if (!open || frame) {
        return;
      }
      frame = requestAnimationFrame(function () {
        frame = 0;
        if (open) {
          place(open.button, open.panel);
        }
      });
    });
  });
})();
