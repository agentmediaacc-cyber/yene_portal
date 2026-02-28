(function () {
  function qs(sel){ return document.querySelector(sel); }
  function qsa(sel){ return Array.from(document.querySelectorAll(sel)); }

  // Toggle sidebar on mobile
  const burger = qs('.hamburger');
  const sidebar = qs('#sidebar') || qs('.sidebar');

  if (burger && sidebar) {
    burger.addEventListener('click', function () {
      sidebar.classList.toggle('open');
      document.body.classList.toggle('nav-open');
    });
  }

  // Close sidebar after clicking any item (mobile)
  if (sidebar) {
    qsa('.sidebar a, .sidebar .item').forEach(function (el) {
      el.addEventListener('click', function () {
        if (window.innerWidth <= 900) {
          sidebar.classList.remove('open');
          document.body.classList.remove('nav-open');
        }
      });
    });
  }

  // Bottom nav active state based on current hash or path
  function setBottomActive() {
    const hash = window.location.hash || '';
    const path = window.location.pathname || '';
    qsa('.bottom-nav a').forEach(a => a.classList.remove('active'));

    let target = null;
    if (hash) target = qs('.bottom-nav a[href="' + hash + '"]');
    if (!target) target = qs('.bottom-nav a[data-path="' + path + '"]');

    if (target) target.classList.add('active');
  }
  window.addEventListener('hashchange', setBottomActive);
  setBottomActive();
})();
