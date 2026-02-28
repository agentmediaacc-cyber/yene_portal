(function () {
  function onReady(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  onReady(function () {
    const sidebar = document.getElementById("sidebar") || document.querySelector(".sidebar");
    const hamburger = document.querySelector(".hamburger") || document.querySelector("#hamburger") || document.querySelector("[data-hamburger]");
    if (!sidebar || !hamburger) return;

    // Overlay
    let overlay = document.getElementById("menuOverlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "menuOverlay";
      document.body.appendChild(overlay);
    }

    function openMenu() {
      document.documentElement.classList.add("menu-open");
      document.body.classList.add("menu-open");
      sidebar.classList.add("open");
      overlay.classList.add("show");
    }

    function closeMenu() {
      document.documentElement.classList.remove("menu-open");
      document.body.classList.remove("menu-open");
      sidebar.classList.remove("open");
      overlay.classList.remove("show");
    }

    function toggleMenu(e) {
      if (e) { e.preventDefault(); e.stopPropagation(); }
      if (sidebar.classList.contains("open")) closeMenu();
      else openMenu();
    }

    hamburger.addEventListener("click", toggleMenu);
    overlay.addEventListener("click", closeMenu);

    // Close after clicking a menu item
    sidebar.addEventListener("click", function (e) {
      const link = e.target && e.target.closest("a,button");
      if (link) closeMenu();
    });

    // ESC closes
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeMenu();
    });

    // If phone rotates / resize to desktop, close
    window.addEventListener("resize", function () {
      if (window.innerWidth >= 900) closeMenu();
    });
  });
})();
