// ─── LIVE CLOCK (date on top, time below) ───
(function initClock() {
  const el = document.getElementById("live-clock");
  if (!el) return;
  // Build inner structure once
  el.innerHTML = '<span class="navbar-clock-date"></span><span class="navbar-clock-time"></span>';
  const dateEl = el.querySelector(".navbar-clock-date");
  const timeEl = el.querySelector(".navbar-clock-time");
  function tick() {
    const now = new Date();
    const pad = n => String(n).padStart(2, "0");
    dateEl.textContent = `${pad(now.getDate())}.${pad(now.getMonth()+1)}.${now.getFullYear()}`;
    timeEl.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  }
  tick();
  setInterval(tick, 1000);
}());

// ─── FOOTER HIDE AT BOTTOM ───
(function initFooterHide() {
  const footer = document.querySelector(".site-footer");
  if (!footer) return;
  let ticking = false;
  function checkScroll() {
    // Hide footer when within ~60 px of the document bottom
    const scrollBottom = window.scrollY + window.innerHeight;
    const docHeight = document.documentElement.scrollHeight;
    if (docHeight - scrollBottom < 60) {
      footer.classList.add("footer-hidden");
    } else {
      footer.classList.remove("footer-hidden");
    }
    ticking = false;
  }
  window.addEventListener("scroll", function () {
    if (!ticking) { requestAnimationFrame(checkScroll); ticking = true; }
  }, { passive: true });
  checkScroll();
}());

// ─── HAMBURGER MENU ───
const hamburger = document.getElementById("hamburger");
const navLinks  = document.getElementById("nav-links");
if (hamburger && navLinks) {
  hamburger.addEventListener("click", () => {
    navLinks.classList.toggle("open");
  });
}

// ─── LIVE SEARCH ───
const searchInput = document.getElementById("search-input");
if (searchInput) {
  searchInput.addEventListener("input", function () {
    const q = this.value.toLowerCase().trim();
    const rows = document.querySelectorAll("tbody tr.data-row");
    let visible = 0;
    rows.forEach(row => {
      const text = row.dataset.search || "";
      if (!q || text.includes(q)) {
        row.classList.remove("hidden-row");
        visible++;
      } else {
        row.classList.add("hidden-row");
      }
    });
    const info = document.getElementById("table-info");
    if (info) {
      info.textContent = `Showing ${visible} of ${rows.length} entries`;
    }
  });
}

// ─── DATE TABS ───
function activateTab(tabEl, colClass) {
  // deactivate all tabs
  document.querySelectorAll(".date-tab").forEach(t => t.classList.remove("active"));
  tabEl.classList.add("active");

  // hide all round columns and total columns first
  document.querySelectorAll(".round-col").forEach(col => col.style.display = "none");
  document.querySelectorAll(".total-col").forEach(c => c.style.display = "none");

  if (colClass === "total") {
    // Total tab: show all day-time columns but NOT per-day pigeons columns
    document.querySelectorAll(".round-col").forEach(col => {
      if (!col.classList.contains("pigeons-day-col")) {
        col.style.display = "";
      }
    });
    // Show total columns (total time + total pigeons)
    document.querySelectorAll(".total-col").forEach(c => c.style.display = "");
  } else {
    // Specific day tab: show that day's time + pigeons columns, hide total cols
    document.querySelectorAll(`.${colClass}`).forEach(c => c.style.display = "");
  }
}

document.querySelectorAll(".date-tab").forEach(tab => {
  tab.addEventListener("click", function () {
    const col = this.dataset.col;
    activateTab(this, col);
  });
});

// activate Total tab by default
const totalTab = document.querySelector(".date-tab[data-col='total']");
if (totalTab) activateTab(totalTab, "total");

// ─── TIME INPUT FORMAT HELPER ───
document.querySelectorAll(".time-input").forEach(input => {
  input.addEventListener("blur", function () {
    const val = this.value.trim();
    if (!val) return;
    const parts = val.split(":");
    if (parts.length === 3) {
      const h = parts[0].padStart(2, "0");
      const m = parts[1].padStart(2, "0");
      const s = parts[2].padStart(2, "0");
      this.value = `${h}:${m}:${s}`;
    }
  });
});

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

  previousButton?.addEventListener("click", () => {
    showSlide(activeSlide - 1);
    startCarousel();
  });
  nextButton?.addEventListener("click", () => {
    showSlide(activeSlide + 1);
    startCarousel();
  });
  indicators.forEach((indicator) => {
    indicator.addEventListener("click", () => {
      showSlide(Number(indicator.dataset.carouselIndicator));
      startCarousel();
    });
  });

  homeCarousel.addEventListener("mouseenter", stopCarousel);
  homeCarousel.addEventListener("mouseleave", startCarousel);
  homeCarousel.addEventListener("focusin", stopCarousel);
  homeCarousel.addEventListener("focusout", (event) => {
    if (!homeCarousel.contains(event.relatedTarget)) startCarousel();
  });
  startCarousel();
}
