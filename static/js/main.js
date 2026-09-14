// ─── LIVE CLOCK ───
function updateClock() {
  const el = document.getElementById("live-clock");
  if (!el) return;
  const now = new Date();
  const pad = n => String(n).padStart(2, "0");
  el.textContent =
    `${now.getDate().toString().padStart(2,"0")}.${pad(now.getMonth()+1)}.${now.getFullYear()}  ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}
setInterval(updateClock, 1000);
updateClock();

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

  // show/hide columns
  const allCols = document.querySelectorAll(".round-col");
  allCols.forEach(col => col.style.display = "none");

  if (colClass === "total") {
    // show all round columns and total
    allCols.forEach(col => col.style.display = "");
    document.querySelectorAll(".total-col").forEach(c => c.style.display = "");
  } else {
    // show only the selected round column + always-visible cols
    document.querySelectorAll(`.${colClass}`).forEach(c => c.style.display = "");
    document.querySelectorAll(".total-col").forEach(c => c.style.display = "none");
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
