// ─── LIVE CLOCK (date on top, time below) ───
(function initClock() {
  const el = document.getElementById("live-clock");
  if (!el) return;
  el.innerHTML = '<span class="navbar-clock-date"></span><span class="navbar-clock-time"></span>';
  const dateEl = el.querySelector(".navbar-clock-date");
  const timeEl = el.querySelector(".navbar-clock-time");
  function tick() {
    const now = new Date();
    const pad = n => String(n).padStart(2, "0");
    dateEl.textContent = `${pad(now.getDate())}.${pad(now.getMonth() + 1)}.${now.getFullYear()}`;
    timeEl.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  }
  tick();
  setInterval(tick, 1000);
}());

// ─── CREDIT BAR ───
// Phones: show it for a few seconds, then get it out of the way. Any scroll
// hides it immediately; when scrolling stops it comes back briefly and hides
// again, so it never sits on top of the table the person is reading.
// Desktop: there is room for it, so it just stays put.
(function initFooterAutoHide() {
  const footer = document.querySelector(".site-footer");
  if (!footer) return;

  const SHOW_MS = 3000;
  const mobile = () => window.matchMedia("(max-width: 768px)").matches;
  let hideTimer = null;

  const hide = () => footer.classList.add("footer-hidden");
  const show = () => footer.classList.remove("footer-hidden");

  function showThenHide() {
    show();
    window.clearTimeout(hideTimer);
    hideTimer = window.setTimeout(() => { if (mobile()) hide(); }, SHOW_MS);
  }

  let scrollTimer = null;
  window.addEventListener("scroll", () => {
    if (!mobile()) { show(); return; }
    hide();
    window.clearTimeout(scrollTimer);
    scrollTimer = window.setTimeout(showThenHide, 260);
  }, { passive: true });

  window.addEventListener("resize", () => {
    if (mobile()) showThenHide();
    else { window.clearTimeout(hideTimer); show(); }
  });

  if (mobile()) showThenHide();
}());

// ─── HAMBURGER MENU ───
const hamburger = document.getElementById("hamburger");
const navLinks = document.getElementById("nav-links");
if (hamburger && navLinks) {
  hamburger.addEventListener("click", () => {
    const open = navLinks.classList.toggle("open");
    hamburger.setAttribute("aria-expanded", open ? "true" : "false");
  });
  document.addEventListener("click", (event) => {
    if (!navLinks.classList.contains("open")) return;
    if (navLinks.contains(event.target) || hamburger.contains(event.target)) return;
    navLinks.classList.remove("open");
    hamburger.setAttribute("aria-expanded", "false");
  });
}

// ─── BACK BUTTON ───
// Falls back to the home page when the tab was opened straight onto this page.
document.querySelectorAll("[data-back]").forEach((button) => {
  button.addEventListener("click", () => {
    if (window.history.length > 1) window.history.back();
    else window.location.href = "/";
  });
});

// ─── LIVE SEARCH ───
const searchInput = document.getElementById("search-input");
if (searchInput) {
  searchInput.addEventListener("input", function () {
    const q = this.value.toLowerCase().trim();
    const rows = document.querySelectorAll("tbody tr.data-row");
    let visible = 0;
    rows.forEach(row => {
      const text = row.dataset.search || "";
      if (!q || text.includes(q)) { row.classList.remove("hidden-row"); visible++; }
      else { row.classList.add("hidden-row"); }
    });
    const info = document.getElementById("table-info");
    if (info) info.textContent = `Showing ${visible} of ${rows.length} entries`;
  });
}

// ─── LEADERBOARD TABS ───
// Total   : pigeons home, each day's total, grand total.
// A day   : that day's pigeon arrival times, then the day total.
// Result  : final standings, only once the tournament is published.
// Rows re-sort on every switch, so SR always follows the column on show.
(function initLeaderboardTabs() {
  const tabs = Array.from(document.querySelectorAll(".date-tab"));
  if (!tabs.length) return;

  const table = document.querySelector(".leaderboard-table");
  const tbody = table ? table.querySelector("tbody") : null;
  const leaderboardPanel = document.getElementById("leaderboard-panel");
  const resultPanel = document.getElementById("result-panel");
  const tableInfo = document.getElementById("table-info");
  const dayHint = document.getElementById("day-hint");
  const searchWrap = document.querySelector(".table-toolbar");

  function sortRows(key) {
    if (!tbody) return;
    const rows = Array.from(tbody.querySelectorAll("tr.data-row"));
    const value = (row) => Number(
      key === "total" ? row.dataset.total : row.dataset["day" + key]
    ) || 0;
    rows.sort((a, b) => value(b) - value(a));
    rows.forEach((row, index) => {
      tbody.appendChild(row);
      const sr = row.querySelector(".sr-cell");
      if (sr) sr.textContent = index + 1;
    });
  }

  function activate(tab) {
    const col = tab.dataset.col;
    tabs.forEach(t => {
      t.classList.toggle("active", t === tab);
      t.setAttribute("aria-selected", t === tab ? "true" : "false");
    });

    const showingResult = col === "result";
    if (resultPanel) resultPanel.hidden = !showingResult;
    if (leaderboardPanel) leaderboardPanel.hidden = showingResult;
    if (tableInfo) tableInfo.hidden = showingResult;
    if (searchWrap) searchWrap.hidden = showingResult;
    if (showingResult) return;

    document.querySelectorAll(".round-col").forEach(c => { c.style.display = "none"; });
    document.querySelectorAll(".total-col").forEach(c => { c.style.display = "none"; });

    if (col === "total") {
      document.querySelectorAll(".total-col").forEach(c => { c.style.display = ""; });
      sortRows("total");
      if (dayHint) dayHint.textContent = "";
    } else {
      document.querySelectorAll("." + col).forEach(c => { c.style.display = ""; });
      sortRows(tab.dataset.day);
      if (dayHint) dayHint.textContent = "Arrival times for day " + tab.dataset.day;
    }

    if (tab.scrollIntoView) {
      tab.scrollIntoView({ inline: "nearest", block: "nearest", behavior: "smooth" });
    }
  }

  tabs.forEach(tab => tab.addEventListener("click", () => activate(tab)));

  const initial = document.querySelector(".date-tab[data-col='total']") || tabs[0];
  if (initial) activate(initial);
}());

// ─── ADMIN: PER-PIGEON TIME ENTRY ───
// Works out each flight and the day total as the admin types, using the same
// rule the server uses: arrival clock time minus the day's release time, with a
// bird that sits after midnight counted into the next day.
(function initPigeonEntry() {
  const form = document.getElementById("times-form");
  if (!form || !window.PIGEON_ENTRY) return;

  const config = window.PIGEON_ENTRY;
  const releaseInput = document.getElementById("day_start_time");
  const releaseLabel = document.getElementById("release-label");
  const fallbackRelease = config.releaseTime;

  const toSeconds = (clock) => {
    if (!clock || clock.indexOf(":") === -1) return null;
    const [h, m] = clock.split(":");
    const hours = Number(h), minutes = Number(m);
    if (Number.isNaN(hours) || Number.isNaN(minutes)) return null;
    return hours * 3600 + minutes * 60;
  };

  const release = () => toSeconds((releaseInput && releaseInput.value) || fallbackRelease);

  const fmt = (seconds) => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
  };

  function flight(arrival) {
    const a = toSeconds(arrival), r = release();
    if (a === null || r === null) return null;
    let elapsed = a - r;
    if (elapsed < 0) elapsed += 86400;
    return elapsed;
  }

  function refreshCard(card) {
    const durations = [];
    let landed = 0;
    card.querySelectorAll(".pigeon-field").forEach(field => {
      const arrivalInput = field.querySelector("[data-role='arrival']");
      const missBox = field.querySelector("[data-role='miss']");
      const flightOut = field.querySelector("[data-role='flight']");
      const missed = missBox && missBox.checked;
      const isExtra = field.classList.contains("is-extra");

      arrivalInput.disabled = !!missed;
      field.classList.toggle("is-missed", !!missed);

      const seconds = missed ? null : flight(arrivalInput.value);
      if (seconds !== null && seconds !== undefined && arrivalInput.value) {
        // The extra bird only joins the total when the tournament says so.
        if (!isExtra || config.extraCounts) durations.push(seconds);
        landed += 1;
        flightOut.textContent = fmt(seconds);
      } else {
        flightOut.textContent = missed ? "bad luck" : "";
      }
    });

    durations.sort((a, b) => b - a);
    const counted = config.scoringLimit > 0 ? durations.slice(0, config.scoringLimit) : durations;
    const total = counted.reduce((sum, value) => sum + value, 0);

    const totalOut = card.querySelector("[data-role='day-total']");
    const landedOut = card.querySelector("[data-role='landed-count']");
    if (totalOut) totalOut.textContent = total ? fmt(total) : "00:00";
    if (landedOut) landedOut.textContent = landed;
  }

  const refreshAll = () => form.querySelectorAll(".entry-card").forEach(refreshCard);

  form.addEventListener("input", (event) => {
    const card = event.target.closest(".entry-card");
    if (card) refreshCard(card);
  });
  form.addEventListener("change", (event) => {
    const card = event.target.closest(".entry-card");
    if (card) refreshCard(card);
  });

  if (releaseInput) {
    releaseInput.addEventListener("change", () => {
      if (releaseLabel) releaseLabel.textContent = releaseInput.value || fallbackRelease;
      refreshAll();
    });
  }

  form.addEventListener("click", (event) => {
    const action = event.target.dataset ? event.target.dataset.action : null;
    if (!action) return;
    const card = event.target.closest(".entry-card");
    if (!card) return;

    if (action === "miss-rest") {
      card.querySelectorAll(".pigeon-field").forEach(field => {
        const arrivalInput = field.querySelector("[data-role='arrival']");
        const missBox = field.querySelector("[data-role='miss']");
        if (!arrivalInput.value && missBox) missBox.checked = true;
      });
    }
    if (action === "clear-row") {
      card.querySelectorAll(".pigeon-field").forEach(field => {
        field.querySelector("[data-role='arrival']").value = "";
        const missBox = field.querySelector("[data-role='miss']");
        if (missBox) missBox.checked = false;
      });
    }
    refreshCard(card);
  });

  // A disabled input is never posted, so re-enable everything on submit and let
  // the server read the bad-luck tick instead.
  form.addEventListener("submit", () => {
    form.querySelectorAll("[data-role='arrival']").forEach(input => { input.disabled = false; });
  });

  refreshAll();
}());

// ─── CONFIRM DELETE ───
document.querySelectorAll(".confirm-delete").forEach(form => {
  form.addEventListener("submit", function (e) {
    if (!confirm("Are you sure you want to delete this? This cannot be undone.")) {
      e.preventDefault();
    }
  });
});

// ─── HOME BANNER CAROUSEL ───
const homeCarousel = document.querySelector("[data-home-carousel]");
if (homeCarousel) {
  const slides = Array.from(homeCarousel.querySelectorAll("[data-carousel-slide]"));
  const indicators = Array.from(homeCarousel.querySelectorAll("[data-carousel-indicator]"));
  const previousButton = homeCarousel.querySelector("[data-carousel-previous]");
  const nextButton = homeCarousel.querySelector("[data-carousel-next]");
  let activeSlide = 0;
  let carouselTimer = null;

  const showSlide = (index) => {
    activeSlide = (index + slides.length) % slides.length;
    slides.forEach((slide, slideIndex) => {
      const isActive = slideIndex === activeSlide;
      slide.hidden = !isActive;
      slide.classList.toggle("is-active", isActive);
    });
    indicators.forEach((indicator, indicatorIndex) => {
      const isActive = indicatorIndex === activeSlide;
      indicator.classList.toggle("is-active", isActive);
      indicator.setAttribute("aria-current", isActive ? "true" : "false");
    });
  };

  const stopCarousel = () => {
    if (carouselTimer) window.clearInterval(carouselTimer);
    carouselTimer = null;
  };

  const startCarousel = () => {
    if (slides.length < 2 || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    stopCarousel();
    carouselTimer = window.setInterval(() => showSlide(activeSlide + 1), 5000);
  };

  previousButton?.addEventListener("click", () => { showSlide(activeSlide - 1); startCarousel(); });
  nextButton?.addEventListener("click", () => { showSlide(activeSlide + 1); startCarousel(); });
  indicators.forEach((indicator) => {
    indicator.addEventListener("click", () => {
      showSlide(Number(indicator.dataset.carouselIndicator));
      startCarousel();
    });
  });

  // Swipe on touch screens.
  let touchStartX = null;
  homeCarousel.addEventListener("touchstart", (e) => { touchStartX = e.touches[0].clientX; }, { passive: true });
  homeCarousel.addEventListener("touchend", (e) => {
    if (touchStartX === null) return;
    const delta = e.changedTouches[0].clientX - touchStartX;
    if (Math.abs(delta) > 40) { showSlide(activeSlide + (delta < 0 ? 1 : -1)); startCarousel(); }
    touchStartX = null;
  }, { passive: true });

  homeCarousel.addEventListener("mouseenter", stopCarousel);
  homeCarousel.addEventListener("mouseleave", startCarousel);
  homeCarousel.addEventListener("focusin", stopCarousel);
  homeCarousel.addEventListener("focusout", (event) => {
    if (!homeCarousel.contains(event.relatedTarget)) startCarousel();
  });
  startCarousel();
}
