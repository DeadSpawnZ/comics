/*
 * Achievement medals on a profile: a floating detail card on hover, keyboard focus and tap.
 *
 * Each medal button names a <template> (data-achievement-detail) rendered and escaped by the
 * server; its content is cloned into the shared popover, so no markup is ever built from text
 * here. The card opens above the medal (below when there is no room), clamped to the viewport.
 * A card opened by hover stays while the pointer is on the medal or on the card itself (WCAG
 * 1.4.13); a click, tap or Enter/Space pins it, and the same action on the same medal closes it.
 * Esc, focus leaving the medal or a press anywhere else close it too.
 */
(function () {
  "use strict";

  const popover = document.getElementById("achievement-popover");
  if (!popover) {
    return;
  }
  // Positioned against the document, out of any card that could clip it.
  document.body.appendChild(popover);

  const GAP = 12; // between the medal and the card (room for the arrow)
  const MARGIN = 16; // minimum distance to the viewport edges
  let current = null;
  let pinned = false;

  function show(button) {
    const template = document.getElementById(button.dataset.achievementDetail);
    if (!template) {
      return;
    }
    if (current && current !== button) {
      current.removeAttribute("aria-describedby");
      pinned = false;
    }
    current = button;
    popover.replaceChildren(template.content.cloneNode(true));
    popover.hidden = false;

    const rect = button.getBoundingClientRect();
    const viewportWidth = document.documentElement.clientWidth;
    const width = popover.offsetWidth;
    const height = popover.offsetHeight;
    const centre = rect.left + rect.width / 2;
    const left = Math.max(MARGIN, Math.min(centre - width / 2, viewportWidth - width - MARGIN));
    let top = rect.top - height - GAP;
    const below = top < MARGIN / 2;
    if (below) {
      top = rect.bottom + GAP;
    }
    popover.classList.toggle("is-below", below);
    popover.style.left = left + window.scrollX + "px";
    popover.style.top = top + window.scrollY + "px";
    popover.style.setProperty("--arrow-x", centre - left + "px");
    button.setAttribute("aria-describedby", popover.id);
  }

  function hide() {
    if (current) {
      current.removeAttribute("aria-describedby");
    }
    current = null;
    pinned = false;
    popover.hidden = true;
    popover.replaceChildren();
  }

  // A hover card closes when the pointer leaves both the medal and the card (the card's
  // transparent bridge over the gap keeps the way between them hoverable).
  function leaving(event) {
    const to = event.relatedTarget;
    const staying = to instanceof Node && (popover.contains(to) || (current && current.contains(to)));
    if (event.pointerType === "mouse" && !pinned && current && !staying) {
      hide();
    }
  }

  popover.addEventListener("pointerleave", leaving);

  document.querySelectorAll("button[data-achievement-detail]").forEach(function (button) {
    button.addEventListener("pointerenter", function (event) {
      if (event.pointerType === "mouse" && !pinned) {
        show(button);
      }
    });
    button.addEventListener("pointerleave", leaving);
    // Keyboard focus only: a click or tap also focuses the button, and is handled below.
    button.addEventListener("focus", function () {
      if (button.matches(":focus-visible")) {
        show(button);
      }
    });
    button.addEventListener("blur", function (event) {
      if (current === button && !popover.contains(event.relatedTarget)) {
        hide();
      }
    });
    button.addEventListener("click", function (event) {
      // Enter/Space (detail 0) toggle the card focus already opened; a click or tap on a card
      // opened by hover pins it instead, and a second one closes it.
      const keyboard = event.detail === 0;
      if (current === button && (pinned || keyboard)) {
        hide();
        return;
      }
      show(button);
      pinned = true;
    });
  });

  // pointerdown rather than click: iOS Safari does not fire click on non-clickable areas.
  document.addEventListener("pointerdown", function (event) {
    const target = event.target;
    if (!current || !(target instanceof Element)) {
      return;
    }
    if (!popover.contains(target) && !target.closest("button[data-achievement-detail]")) {
      hide();
    }
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && current) {
      hide();
    }
  });
  window.addEventListener("resize", function () {
    if (current) {
      hide();
    }
  });
})();
