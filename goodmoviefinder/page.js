    const search = document.getElementById("search")
      || { value: "", addEventListener() {}, focus() {} };
    const searchCount = document.getElementById("search-count");
    const searchClear = document.getElementById("search-clear");
    const searchField = document.querySelector(".search-field");
    const listEmpty = document.getElementById("list-empty");
    const calendarEmpty = document.getElementById("calendar-empty");
    let sectionFilter = null;
    let scrolledSection = null;

    let activeTab = null;
    let watchlistFilter = "all";

    function isCalendar() { return activeTab === "calendar"; }
    function isPlan() { return activeTab === "plan"; }
    function isWatchlist() { return activeTab === "watchlist"; }
    function currentTab() {
      const selected = document.querySelector('[role="tab"][aria-selected="true"]');
      return selected ? selected.getAttribute("data-tab") || "" : "";
    }

    function syncSectionLinks() {
      document.querySelectorAll(".nav-pills a[href^='#']").forEach((link) => {
        const id = link.getAttribute("href").slice(1);
        const current = isCalendar() ? sectionFilter === id : scrolledSection === id;
        if (current) link.setAttribute("aria-current", "true");
        else link.removeAttribute("aria-current");
      });
    }

    function updateScrolledSection() {
      if (activeTab !== "list" && activeTab !== "recommendations") return;
      const marker = (document.querySelector(".tabs-bar")?.getBoundingClientRect().bottom || 0) + 16;
      const sections = [...document.querySelectorAll(".program-section")].filter(
        (section) => !section.hidden && section.offsetParent !== null
      );
      let current = sections[0] ? sections[0].id : null;
      sections.forEach((section) => {
        if (section.getBoundingClientRect().top <= marker) current = section.id;
      });
      scrolledSection = current;
      syncSectionLinks();
    }

    function emptyCopy() {
      const q = search.value.trim();
      if (q && sectionFilter) return "No films match this search in that section.";
      if (q) return "No films match this search.";
      return "No films in this section.";
    }

    function visibleCount(selector) {
      const ids = new Set();
      document.querySelectorAll(selector).forEach((el) => {
        if (!el.hidden) {
          ids.add(
            el.getAttribute("data-plan-id")
            || el.getAttribute("data-movie-id")
            || el.getAttribute("data-stream-id")
          );
        }
      });
      return ids.size;
    }

    function applySearch() {
      const q = search.value.trim().toLowerCase();
      const searching = q.length > 0;
      document.querySelectorAll("[data-movie-row]").forEach((row) => {
        const blob = row.getAttribute("data-search") || "";
        const hide = row.getAttribute("data-lang-hide") === "1" || (searching && !blob.includes(q));
        row.hidden = hide;
        const detail = row.nextElementSibling;
        if (!detail || !detail.hasAttribute("data-movie-detail")) return;
        const expanded = row.querySelector(".expand")?.getAttribute("aria-expanded") === "true";
        detail.hidden = hide || !expanded;
      });
      document.querySelectorAll(".program-section").forEach((section) => {
        const hasMatch = searching && [...section.querySelectorAll("[data-movie-row]")].some((row) => !row.hidden);
        section.classList.toggle("is-search-open", hasMatch);
        syncSection(section);
      });
      document.querySelectorAll("details.section-group").forEach((group) => {
        const hasMatch = searching && [...group.querySelectorAll("[data-movie-row]")].some((row) => !row.hidden);
        if (hasMatch && !group.open) {
          group.open = true;
          group.dataset.autoOpen = "1";
        } else if (!searching && group.dataset.autoOpen) {
          group.open = false;
          delete group.dataset.autoOpen;
        }
      });
      const narrowing = searching || (isCalendar() && !!sectionFilter);
      document.querySelectorAll(".day-film").forEach((film) => {
        const blob = film.getAttribute("data-search") || "";
        const langHide = film.getAttribute("data-lang-hide") === "1";
        const membership = film.getAttribute("data-sections") || film.getAttribute("data-section") || "";
        const sectionMiss = isCalendar() && sectionFilter && !membership.split(/\s+/).includes(sectionFilter);
        const hide = langHide || sectionMiss || (searching && !blob.includes(q));
        film.hidden = hide;
        if (!hide) return;
        const title = film.querySelector(".day-film-title");
        const slot = film.querySelector(".day-film-detail");
        if (title) title.setAttribute("aria-expanded", "false");
        if (slot) {
          slot.hidden = true;
          slot.replaceChildren();
        }
      });
      document.querySelectorAll("[data-day]").forEach((button) => syncDay(button, narrowing));
      document.querySelectorAll(".also-day").forEach((day) => {
        const button = day.querySelector("[data-day]");
        day.hidden = !button || button.disabled;
      });
      const alsoShowing = document.querySelector(".also-showing");
      if (alsoShowing) {
        alsoShowing.hidden = ![...alsoShowing.querySelectorAll(".also-day")].some((day) => !day.hidden);
      }
      document.querySelectorAll("[data-stream-row]").forEach((row) => {
        const blob = row.getAttribute("data-search") || "";
        const hide = (searching && !blob.includes(q))
          || (watchlistFilter === "stream" && row.getAttribute("data-streamable") !== "1")
          || (watchlistFilter === "nonstop" && row.getAttribute("data-nonstop") !== "1");
        row.hidden = hide;
        const detail = row.nextElementSibling;
        if (!detail || !detail.hasAttribute("data-stream-detail")) return;
        const expanded = row.querySelector(".expand")?.getAttribute("aria-expanded") === "true";
        detail.hidden = hide || !expanded;
      });
      document.querySelectorAll("[data-gift-card]").forEach((card) => {
        const blob = card.getAttribute("data-search") || "";
        card.hidden = searching && !blob.includes(q);
      });
      document.querySelectorAll("[data-gift-person]").forEach((person) => {
        const cards = [...person.querySelectorAll("[data-gift-card]")];
        person.hidden = cards.length > 0 && cards.every((card) => card.hidden);
      });
      document.querySelectorAll("[data-gift-block]").forEach((block) => {
        const people = [...block.querySelectorAll("[data-gift-person]")];
        block.hidden = people.length > 0 && people.every((person) => person.hidden);
      });
      const listMatches = visibleCount("[data-movie-row]");
      const dayMatches = visibleCount(".day-film");
      const streamMatches = visibleCount("[data-stream-row]");
      const giftMatches = visibleCount("[data-gift-card]");
      const onFriends = currentTab() === "for-friends";
      const activeMatches = onFriends
        ? giftMatches
        : isCalendar()
          ? dayMatches
          : isWatchlist()
            ? streamMatches
            : listMatches;
      if (searchField) searchField.classList.toggle("is-active", searching);
      if (searchCount) {
        searchCount.hidden = !searching;
        searchCount.textContent = searching
          ? activeMatches + (activeMatches === 1 ? " film" : " films")
          : "";
      }
      if (searchClear) searchClear.hidden = !searching;
      if (listEmpty) {
        listEmpty.hidden = !(searching && listMatches === 0);
        if (searching && listMatches === 0) listEmpty.textContent = emptyCopy();
      }
      if (calendarEmpty) {
        const show = narrowing && dayMatches === 0;
        calendarEmpty.hidden = !show;
        if (show) calendarEmpty.textContent = emptyCopy();
      }
      const streamEmpty = document.getElementById("stream-empty");
      if (streamEmpty) {
        const show = (searching || watchlistFilter !== "all") && streamMatches === 0;
        streamEmpty.hidden = !show;
        if (show) streamEmpty.textContent = "No watchlist films match.";
      }
      const giftEmpty = document.getElementById("gift-empty");
      if (giftEmpty) {
        const show = searching && onFriends && giftMatches === 0 && document.querySelector("[data-gift-card]");
        giftEmpty.hidden = !show;
        if (show) giftEmpty.textContent = "No films match.";
      }
    }

    function closeDay(button) {
      button.setAttribute("aria-expanded", "false");
      const panel = document.getElementById(button.getAttribute("aria-controls"));
      if (panel) panel.hidden = true;
    }

    function bestVisibleTitle(films) {
      let bestTitle = "";
      let bestRating = null;
      films.forEach((film) => {
        if (film.hidden) return;
        const raw = film.getAttribute("data-rating");
        const rating = raw ? Number(raw) : null;
        const title = film.getAttribute("data-title") || "";
        const numeric = rating != null && Number.isFinite(rating);
        if (!bestTitle || (numeric && (bestRating == null || rating > bestRating))) {
          bestTitle = title;
          bestRating = numeric ? rating : bestRating;
        }
      });
      return bestTitle;
    }

    function syncDay(button, searching) {
      const panel = document.getElementById(button.getAttribute("aria-controls"));
      const films = panel ? [...panel.querySelectorAll(".day-film")] : [];
      const visible = films.filter((film) => !film.hidden);
      const countEl = button.querySelector(".month-day-count");
      const titleEl = button.querySelector(".month-day-title");
      const originalTitle = button.getAttribute("data-title") || "";
      if (countEl) countEl.textContent = String(visible.length);
      if (titleEl) titleEl.textContent = bestVisibleTitle(films) || originalTitle;
      const empty = films.length > 0 && visible.length === 0;
      button.classList.toggle("is-filtered-out", empty);
      button.disabled = empty;
      if (empty && button.getAttribute("aria-expanded") === "true") closeDay(button);
    }

    function toggleDay(button) {
      if (button.disabled) return;
      const open = button.getAttribute("aria-expanded") === "true";
      document.querySelectorAll("[data-day][aria-expanded='true']").forEach(closeDay);
      if (open) return;
      button.setAttribute("aria-expanded", "true");
      const panel = document.getElementById(button.getAttribute("aria-controls"));
      if (panel) panel.hidden = false;
    }

    function fillFilmDetail(film) {
      const slot = film.querySelector(".day-film-detail");
      const button = film.querySelector(".day-film-title");
      if (!slot || !button) return;
      const movieId = film.getAttribute("data-movie-id");
      const row = document.querySelector(
        `[data-movie-row][data-movie-id="${CSS.escape(movieId)}"]`
      );
      const source = row && row.nextElementSibling && row.nextElementSibling.querySelector(".detail");
      if (!source) return;
      const clone = source.cloneNode(true);
      clone.querySelectorAll("[id]").forEach((node) => node.removeAttribute("id"));
      slot.replaceChildren(clone);
      slot.hidden = false;
      button.setAttribute("aria-expanded", "true");
      const band = film.closest("[id^='day-']");
      const iso = band && band.id.startsWith("day-") ? band.id.slice(4) : "";
      if (iso) showClickedDayOnly(clone, iso);
      loadBackdrop(clone);
      loadFriends(slot);
    }

    function showDayLabel(iso) {
      const day = new Date(iso + "T12:00:00");
      if (Number.isNaN(day.getTime())) return "";
      const weeks = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
      const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
      const index = (day.getDay() + 6) % 7;
      return weeks[index] + " " + String(day.getDate()).padStart(2, "0") + " " + months[day.getMonth()];
    }

    function showClickedDayOnly(root, iso) {
      const days = [...root.querySelectorAll(".showtime-day")];
      const label = showDayLabel(iso);
      const chosen = days.filter((day) => {
        const dated = day.getAttribute("data-date");
        if (dated) return dated === iso;
        return (day.querySelector("h3")?.textContent || "").trim() === label;
      });
      if (!chosen.length) return;
      days.forEach((day) => {
        day.hidden = !chosen.includes(day);
      });
    }

    function toggleFilmDetail(button) {
      const film = button.closest(".day-film");
      const slot = film && film.querySelector(".day-film-detail");
      if (!film || !slot) return;
      const open = button.getAttribute("aria-expanded") === "true";
      if (open) {
        button.setAttribute("aria-expanded", "false");
        slot.hidden = true;
        slot.replaceChildren();
        return;
      }
      fillFilmDetail(film);
    }

    const FOLD_STORAGE = "gmf-folded-sections";
    const UNFOLD_STORAGE = "gmf-unfolded-sections";

    function readIdSet(key) {
      try {
        const raw = localStorage.getItem(key);
        const ids = raw ? JSON.parse(raw) : [];
        return new Set(Array.isArray(ids) ? ids : []);
      } catch (err) {
        return new Set();
      }
    }

    function writeIdSet(key, ids) {
      try {
        localStorage.setItem(key, JSON.stringify([...ids]));
      } catch (err) {
        /* private mode or storage full */
      }
    }

    const foldedIds = readIdSet(FOLD_STORAGE);
    const unfoldedIds = readIdSet(UNFOLD_STORAGE);

    function sectionIsOpen(section) {
      return !section.classList.contains("is-folded") || section.classList.contains("is-search-open");
    }

    function syncSection(section) {
      const button = section.querySelector(".section-toggle");
      if (button) button.setAttribute("aria-expanded", sectionIsOpen(section) ? "true" : "false");
    }

    function setFolded(section, isFolded) {
      section.classList.toggle("is-folded", isFolded);
      if (isFolded) {
        foldedIds.add(section.id);
        unfoldedIds.delete(section.id);
      } else {
        foldedIds.delete(section.id);
        if (section.hasAttribute("data-start-folded")) unfoldedIds.add(section.id);
        else unfoldedIds.delete(section.id);
      }
      writeIdSet(FOLD_STORAGE, foldedIds);
      writeIdSet(UNFOLD_STORAGE, unfoldedIds);
      syncSection(section);
    }

    function bindProgramSection(section) {
      if (section.dataset.gmfBound) return;
      section.dataset.gmfBound = "1";
      const startsFolded = section.hasAttribute("data-start-folded");
      const folded = foldedIds.has(section.id) || (startsFolded && !unfoldedIds.has(section.id));
      section.classList.toggle("is-folded", folded);
      syncSection(section);
      const button = section.querySelector(".section-toggle");
      if (!button) return;
      button.addEventListener("click", () => {
        setFolded(section, !section.classList.contains("is-folded"));
      });
    }

    document.querySelectorAll(".program-section").forEach(bindProgramSection);

    document.querySelectorAll(".nav-pills a[href^='#']").forEach((link) => {
      link.addEventListener("click", (event) => {
        const id = link.getAttribute("href").slice(1);
        if (isCalendar()) {
          event.preventDefault();
          sectionFilter = sectionFilter === id ? null : id;
          syncSectionLinks();
          applySearch();
          return;
        }
        event.preventDefault();
        const section = document.getElementById(id);
        if (!section) return;
        const group = section.closest("details.section-group");
        if (group) group.open = true;
        if (section.classList.contains("program-section")) setFolded(section, false);
        section.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });

    const posterMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const POSTER_FLIGHT_MS = 320;

    const ROW_POSTER_W = 32;
    const ROW_POSTER_H = 48;
    const ROW_POSTER_WIDE_H = 72;

    function posterBox(rect) {
      return rect.width > 1 && rect.height > 1;
    }

    // Matches cubic-bezier(0.2, 0.8, 0.2, 1). Sampled per frame so scrolling can move the target.
    function posterEase(x) {
      if (x <= 0) return 0;
      if (x >= 1) return 1;
      const x1 = 0.2;
      const y1 = 0.8;
      const x2 = 0.2;
      const y2 = 1;
      const cx = 3 * x1;
      const bx = 3 * (x2 - x1) - cx;
      const ax = 1 - cx - bx;
      const cy = 3 * y1;
      const by = 3 * (y2 - y1) - cy;
      const ay = 1 - cy - by;
      const sampleX = (t) => ((ax * t + bx) * t + cx) * t;
      const sampleDX = (t) => (3 * ax * t + 2 * bx) * t + cx;
      let t = x;
      for (let i = 0; i < 6; i += 1) {
        const slope = sampleDX(t);
        if (Math.abs(slope) < 1e-6) break;
        t -= (sampleX(t) - x) / slope;
      }
      return ((ay * t + by) * t + cy) * t;
    }

    function scrollAnchor(rect, el) {
      const scrolls = [];
      let node = el && el.parentElement;
      while (node && node !== document.body && node !== document.documentElement) {
        const style = getComputedStyle(node);
        if (/(auto|scroll)/.test(style.overflowX + style.overflowY)) {
          scrolls.push({ node, left: node.scrollLeft, top: node.scrollTop });
        }
        node = node.parentElement;
      }
      scrolls.push({ left: window.scrollX, top: window.scrollY });
      return { left: rect.left, top: rect.top, scrolls };
    }

    function anchoredPoint(anchor) {
      let dx = 0;
      let dy = 0;
      for (const item of anchor.scrolls) {
        if (item.node) {
          dx += item.node.scrollLeft - item.left;
          dy += item.node.scrollTop - item.top;
        } else {
          dx += window.scrollX - item.left;
          dy += window.scrollY - item.top;
        }
      }
      return { left: anchor.left - dx, top: anchor.top - dy };
    }

    function livePoint(el, anchor) {
      if (el && el.isConnected) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 || rect.height > 0) return { left: rect.left, top: rect.top };
      }
      return anchoredPoint(anchor);
    }

    function rowPosterTarget(thumb) {
      const rect = thumb.getBoundingClientRect();
      if (thumb.classList.contains("row-poster-wide")) {
        return {
          left: rect.left,
          top: rect.top,
          width: Math.max(rect.width, ROW_POSTER_W),
          height: ROW_POSTER_WIDE_H,
        };
      }
      return {
        left: rect.left,
        top: rect.top,
        width: ROW_POSTER_W,
        height: ROW_POSTER_H,
      };
    }

    function cancelPosterFlight(row) {
      const flight = row._posterFlight;
      if (!flight) return;
      flight.cancelled = true;
      if (flight.frame) cancelAnimationFrame(flight.frame);
      if (flight.clone) flight.clone.remove();
      row._posterFlight = null;
    }

    function cornerRadius(el) {
      const value = parseFloat(getComputedStyle(el).borderTopLeftRadius);
      return Number.isFinite(value) ? value : 0;
    }

    function flyPoster(fromRect, toRect, src, fromRadius, toRadius, fromEl, toEl, done) {
      const flight = { cancelled: false, clone: null, frame: 0 };
      const clone = document.createElement("img");
      flight.clone = clone;
      clone.className = "poster-flight";
      clone.alt = "";
      clone.src = src;
      clone.style.left = "0";
      clone.style.top = "0";
      clone.style.width = fromRect.width + "px";
      clone.style.height = fromRect.height + "px";
      clone.style.transformOrigin = "top left";
      clone.style.borderRadius = fromRadius + "px";
      clone.style.transform = `translate3d(${fromRect.left}px, ${fromRect.top}px, 0) scale(1, 1)`;
      document.body.appendChild(clone);
      const fromAnchor = scrollAnchor(fromRect, fromEl);
      const toAnchor = scrollAnchor(toRect, toEl);
      const started = performance.now();
      const step = (now) => {
        if (flight.cancelled) return;
        const t = Math.min(1, (now - started) / POSTER_FLIGHT_MS);
        const eased = posterEase(t);
        const from = livePoint(fromEl, fromAnchor);
        const to = livePoint(toEl, toAnchor);
        const width = fromRect.width + (toRect.width - fromRect.width) * eased;
        const height = fromRect.height + (toRect.height - fromRect.height) * eased;
        const x = from.left + (to.left - from.left) * eased;
        const y = from.top + (to.top - from.top) * eased;
        const scaleX = fromRect.width ? width / fromRect.width : 1;
        const scaleY = fromRect.height ? height / fromRect.height : 1;
        clone.style.transform = `translate3d(${x}px, ${y}px, 0) scale(${scaleX}, ${scaleY})`;
        const radius = fromRadius + (toRadius - fromRadius) * eased;
        clone.style.borderRadius = `${radius / scaleX}px / ${radius / scaleY}px`;
        if (t < 1) {
          flight.frame = requestAnimationFrame(step);
          return;
        }
        flight.cancelled = true;
        clone.remove();
        done();
      };
      flight.frame = requestAnimationFrame(step);
      return flight;
    }

    function settlePoster(thumb, poster, open) {
      if (thumb) thumb.style.visibility = open ? "hidden" : "";
      if (poster) poster.style.visibility = "";
    }

    function togglePosterRow(row, detail) {
      const button = row.querySelector(".expand");
      if (!button) return;
      const open = button.getAttribute("aria-expanded") !== "true";
      const thumb = row.querySelector(".title-line .row-poster") || row.querySelector(".row-poster");
      const poster = detail.querySelector(".poster");
      cancelPosterFlight(row);
      const canFly = thumb && poster && thumb.getAttribute("src") && !posterMotion.matches;
      if (!canFly) {
        button.setAttribute("aria-expanded", open ? "true" : "false");
        row.classList.toggle("is-open", open);
        detail.hidden = !open;
        settlePoster(thumb, poster, open);
        if (open) {
          loadBackdrop(detail);
          loadFriends(detail);
        }
        return;
      }
      if (open) {
        const from = thumb.getBoundingClientRect();
        button.setAttribute("aria-expanded", "true");
        row.classList.add("is-open");
        thumb.style.visibility = "hidden";
        poster.style.visibility = "hidden";
        detail.hidden = false;
        loadBackdrop(detail);
        loadFriends(detail);
        const to = poster.getBoundingClientRect();
        if (!posterBox(from) || !posterBox(to)) {
          settlePoster(thumb, poster, true);
          return;
        }
        const flight = flyPoster(from, to, thumb.src, cornerRadius(thumb), cornerRadius(poster), thumb, poster, () => {
          if (row._posterFlight !== flight) return;
          poster.style.visibility = "";
          row._posterFlight = null;
        });
        row._posterFlight = flight;
        return;
      }
      const from = poster.getBoundingClientRect();
      const to = rowPosterTarget(thumb);
      if (!posterBox(from) || !posterBox(to)) {
        poster.style.visibility = "hidden";
        detail.hidden = true;
        button.setAttribute("aria-expanded", "false");
        row.classList.remove("is-open");
        settlePoster(thumb, poster, false);
        return;
      }
      poster.style.visibility = "hidden";
      const flight = flyPoster(from, to, thumb.src, cornerRadius(poster), cornerRadius(thumb), poster, thumb, () => {
        if (row._posterFlight !== flight) return;
        settlePoster(thumb, poster, false);
        row._posterFlight = null;
      });
      row._posterFlight = flight;
      detail.hidden = true;
      button.setAttribute("aria-expanded", "false");
      row.classList.remove("is-open");
    }

    function bindMovieRow(row) {
      if (row.dataset.gmfBound) return;
      row.dataset.gmfBound = "1";
      row.addEventListener("click", (event) => {
        if (event.target.closest(".plan-add, .match-repair")) return;
        const detail = row.nextElementSibling;
        if (!detail || !detail.hasAttribute("data-movie-detail")) return;
        togglePosterRow(row, detail);
      });
    }

    document.querySelectorAll("[data-movie-row]").forEach(bindMovieRow);

    document.querySelectorAll("[data-stream-row]").forEach((row) => {
      if (row.dataset.gmfBound) return;
      row.dataset.gmfBound = "1";
      row.addEventListener("click", (event) => {
        if (event.target.closest("a")) return;
        const detail = row.nextElementSibling;
        if (!detail || !detail.hasAttribute("data-stream-detail")) return;
        togglePosterRow(row, detail);
      });
    });

    const friendLoads = new Map();
    const friendQueue = [];
    let friendBusy = false;

    function viewerIsSignedIn() {
      try {
        const node = document.getElementById("viewer-config");
        const parsed = JSON.parse((node && node.textContent) || "{}");
        return Boolean(parsed && parsed.signedIn && parsed.letterboxdUser);
      } catch (err) {
        return false;
      }
    }

    function friendFetchErrorMessage(err) {
      if (err && typeof err.message === "string" && err.message.trim()) {
        return err.message.trim();
      }
      if (err && err.name === "TypeError") {
        return "Letterboxd could not be reached. Try again in a moment.";
      }
      return "Friend ratings could not be loaded.";
    }

    function fetchFriendRatings(slug, retriesLeft) {
      const tries = retriesLeft === undefined ? 4 : retriesLeft;
      const request = () =>
        fetch("/api/friends?slug=" + encodeURIComponent(slug)).then(async (response) => {
          if (!response.ok) {
            let message = "Friend ratings could not be loaded.";
            try {
              const data = await response.json();
              if (data && typeof data.error === "string" && data.error.trim()) {
                message = data.error.trim();
              }
            } catch (err) {
              /* ignore */
            }
            throw new Error(message);
          }
          return response.json();
        });
      const attempt = (left) =>
        request().catch((err) => {
          const normalized =
            err instanceof Error ? err : new Error(friendFetchErrorMessage(err));
          if (normalized.message === "Failed to fetch") {
            normalized.message = "Letterboxd could not be reached. Try again in a moment.";
          }
          if (left <= 0) throw normalized;
          const wait = 1500 * (5 - left);
          return new Promise((resolve) => setTimeout(resolve, wait)).then(() =>
            attempt(left - 1)
          );
        });
      return attempt(tries);
    }

    function promoteFriendSlug(slug) {
      const index = friendQueue.findIndex((item) => item.slug === slug);
      if (index > 0) {
        const item = friendQueue.splice(index, 1)[0];
        friendQueue.unshift(item);
      }
    }

    function pumpFriendQueue() {
      if (friendBusy || !friendQueue.length) return;
      const item = friendQueue.shift();
      friendBusy = true;
      fetchFriendRatings(item.slug)
        .then((payload) => item.resolve(payload))
        .catch((err) => {
          friendLoads.delete(item.slug);
          item.reject(err);
        })
        .finally(() => {
          friendBusy = false;
          pumpFriendQueue();
        });
    }

    function enqueueFriendRatings(slug, priority) {
      const existing = friendLoads.get(slug);
      if (existing) {
        if (priority) promoteFriendSlug(slug);
        return existing;
      }
      let resolve;
      let reject;
      const job = new Promise((res, rej) => {
        resolve = res;
        reject = rej;
      });
      friendLoads.set(slug, job);
      const item = { slug, resolve, reject };
      if (priority) friendQueue.unshift(item);
      else friendQueue.push(item);
      pumpFriendQueue();
      return job;
    }

    function startFriendLoad(slug, priority) {
      if (!slug) return;
      enqueueFriendRatings(slug, priority)
        .then((payload) => renderFriends(slug, payload))
        .catch((err) => markFriendError(slug, err && err.message));
    }

    function friendPanels(slug) {
      return [...document.querySelectorAll("[data-friend-slug]")].filter(
        (panel) => panel.getAttribute("data-friend-slug") === slug
      );
    }

    function allowedUrl(value, prefixes) {
      if (typeof value !== "string" || !prefixes.some((prefix) => value.startsWith(prefix))) {
        return "";
      }
      try {
        const url = new URL(value);
        return url.protocol === "https:" ? value : "";
      } catch (err) {
        return "";
      }
    }

    function friendGroup(panel, section) {
      if (!panel) return null;
      const group = panel.querySelector('[data-friend-section="' + section + '"]');
      if (!group) return null;
      return {
        group,
        list: group.querySelector(".friend-list"),
        empty: group.querySelector(".friend-empty"),
      };
    }

    function appendFriendRow(list, friend, options) {
      options = options || {};
      if (!friend || typeof friend.name !== "string" || !list) return;
      const item = document.createElement("li");
      item.className = options.commentCard ? "friend is-comment" : "friend";
      const profileUrl = allowedUrl(friend.profile, ["https://letterboxd.com/"]);
      const avatarUrl = allowedUrl(friend.avatar, [
        "https://a.ltrbxd.com/",
        "https://s.ltrbxd.com/",
      ]);
      if (profileUrl && avatarUrl) {
        const avatarLink = document.createElement("a");
        avatarLink.className = "friend-avatar-link";
        avatarLink.href = profileUrl;
        avatarLink.target = "_blank";
        avatarLink.rel = "noopener noreferrer";
        avatarLink.setAttribute("aria-label", friend.name);
        const image = document.createElement("img");
        image.className = "friend-avatar";
        image.src = avatarUrl;
        image.alt = "";
        image.width = 40;
        image.height = 40;
        avatarLink.appendChild(image);
        item.appendChild(avatarLink);
      }
      const body = document.createElement("div");
      body.className = "friend-body";
      const top = document.createElement("div");
      top.className = "friend-top";
      const name = document.createElement(profileUrl ? "a" : "span");
      name.className = "friend-name";
      name.textContent = friend.name;
      if (profileUrl) {
        name.href = profileUrl;
        name.target = "_blank";
        name.rel = "noopener noreferrer";
      }
      top.appendChild(name);
      if (
        options.showRating
        && typeof friend.ratingText === "string"
        && /^\d(?:\.\d)?$/.test(friend.ratingText)
      ) {
        const badge = document.createElement("span");
        badge.className = "rating-badge";
        badge.textContent = friend.ratingText;
        if (
          typeof friend.ratingStyle === "string"
          && /^color: rgb\(\d{1,3},\d{1,3},\d{1,3}\); background: rgba\(\d{1,3},\d{1,3},\d{1,3},[\d.]+\);$/.test(friend.ratingStyle)
        ) {
          badge.style.cssText = friend.ratingStyle;
        }
        top.appendChild(badge);
      } else if (options.watchlistBadge) {
        const badge = document.createElement("span");
        badge.className = "friend-watchlist-badge";
        badge.textContent = "Watchlist";
        top.appendChild(badge);
      }
      body.appendChild(top);
      if (options.showComment && typeof friend.comment === "string" && friend.comment.trim()) {
        const comment = document.createElement("p");
        comment.className = "friend-comment";
        comment.textContent = friend.comment;
        body.appendChild(comment);
      }
      item.appendChild(body);
      list.appendChild(item);
    }

    function renderFriendSection(section, friends, options) {
      options = options || {};
      if (!section || !section.list) return;
      const hasFriends = friends.length > 0;
      if (options.hideIfEmpty && !hasFriends) {
        section.group.hidden = true;
        section.list.replaceChildren();
        section.list.hidden = true;
        return;
      }
      section.list.replaceChildren();
      friends.forEach((friend) => appendFriendRow(section.list, friend, options));
      section.list.hidden = !hasFriends;
      if (section.empty) section.empty.hidden = hasFriends;
      section.group.hidden = false;
    }

    function renderFriends(slug, payload) {
      const friends = Array.isArray(payload.friends) ? payload.friends : [];
      const watchlist = Array.isArray(payload.watchlist) ? payload.watchlist : [];
      friendPanels(slug).forEach((panel) => {
        const status = panel.querySelector(".friend-status");
        const comments = friendGroup(panel, "comments");
        const rated = friendGroup(panel, "rated");
        const listed = friendGroup(panel, "watchlist");
        const commented = friends.filter(
          (friend) => typeof friend.comment === "string" && friend.comment.trim()
        );
        const legacyList = panel.querySelector(":scope > .friend-list");
        if (!rated && legacyList) {
          legacyList.replaceChildren();
          if (!friends.length) {
            if (status) {
              status.hidden = false;
              status.textContent = "None of your Letterboxd friends have rated this.";
            }
            legacyList.hidden = true;
          } else {
            if (status) status.hidden = true;
            friends.forEach((friend) =>
              appendFriendRow(legacyList, friend, { showRating: true, showComment: true })
            );
            legacyList.hidden = false;
          }
          panel.dataset.friendState = "ready";
          renderGiftPicker(panel, slug, { friends, watchlist });
          return;
        }
        if (status) status.hidden = true;
        renderFriendSection(comments, commented, {
          showRating: true,
          showComment: true,
          commentCard: true,
          hideIfEmpty: true,
        });
        renderFriendSection(rated, friends, { showRating: true, hideIfEmpty: true });
        renderFriendSection(listed, watchlist, { watchlistBadge: true, hideIfEmpty: true });
        if (!friends.length && !watchlist.length && status) {
          status.hidden = false;
          status.textContent =
            "None of your Letterboxd friends have rated this or have it on their watchlist.";
        }
        panel.dataset.friendState = "ready";
        renderGiftPicker(panel, slug, { friends, watchlist });
      });
      latestFriendPayloads.set(slug, { friends, watchlist });
    }

    let giftsPayload = null;
    let giftsLoad = null;
    let followingForGifts = null;
    let followingForGiftsLoad = null;
    const latestFriendPayloads = new Map();

    function signedInUsername() {
      try {
        const node = document.getElementById("viewer-config");
        const parsed = JSON.parse((node && node.textContent) || "{}");
        return String((parsed && parsed.letterboxdUser) || "");
      } catch (err) {
        return "";
      }
    }

    function giftSection(panel) {
      return panel ? panel.querySelector('[data-friend-section="gift"]') : null;
    }

    function filmFacts(panel) {
      const detail = panel.closest(".detail");
      const poster = detail ? detail.querySelector(".poster") : null;
      const link = detail ? detail.querySelector(".link-lb") : null;
      const title = ((poster && poster.alt) || (link && link.textContent) || "").trim();
      let year = null;
      const fact = detail ? detail.querySelector(".detail-facts span") : null;
      if (fact && /^\d{4}$/.test(fact.textContent.trim())) year = Number(fact.textContent.trim());
      const src = poster ? poster.getAttribute("src") || "" : "";
      return {
        title,
        year,
        poster: allowedUrl(src, ["https://a.ltrbxd.com/resized/"]),
      };
    }

    function excludedUsernames(payload) {
      const skip = new Set();
      const self = signedInUsername().toLowerCase();
      if (self) skip.add(self);
      (payload.friends || []).forEach((friend) => {
        if (friend && typeof friend.rating === "number") {
          skip.add(String(friend.username || "").toLowerCase());
        }
      });
      (payload.watchlist || []).forEach((friend) => {
        if (friend) skip.add(String(friend.username || "").toLowerCase());
      });
      return skip;
    }

    function giftedUsernames(slug) {
      const names = new Set();
      const outgoing = giftsPayload && Array.isArray(giftsPayload.outgoing) ? giftsPayload.outgoing : [];
      outgoing.forEach((group) => {
        const films = Array.isArray(group.films) ? group.films : [];
        if (films.some((film) => film && film.slug === slug)) {
          names.add(String(group.username || "").toLowerCase());
        }
      });
      return names;
    }

    function refreshGiftSurfaces() {
      if (giftsPayload) renderGiftBoard(giftsPayload);
      latestFriendPayloads.forEach((payload, slug) => {
        friendPanels(slug).forEach((panel) => renderGiftPicker(panel, slug, payload));
      });
    }

    function takeGifts(payload) {
      giftsPayload = {
        outgoing: Array.isArray(payload.outgoing) ? payload.outgoing : [],
        incoming: Array.isArray(payload.incoming) ? payload.incoming : [],
      };
      refreshGiftSurfaces();
      return giftsPayload;
    }

    function ensureGifts() {
      if (giftsPayload) return Promise.resolve(giftsPayload);
      if (!giftsLoad) {
        giftsLoad = fetch("/api/gifts")
          .then(async (response) => {
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
              const message = data && typeof data.error === "string" && data.error.trim()
                ? data.error.trim()
                : "Those lists could not be loaded.";
              throw new Error(message);
            }
            giftsLoad = null;
            return takeGifts(data);
          })
          .catch((err) => {
            giftsLoad = null;
            throw err;
          });
      }
      return giftsLoad;
    }

    function ensureFollowingForGifts() {
      if (followingForGifts) return Promise.resolve(followingForGifts);
      if (!followingForGiftsLoad) {
        followingForGiftsLoad = fetch("/api/following")
          .then(async (response) => {
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
              const message = data && typeof data.error === "string" && data.error.trim()
                ? data.error.trim()
                : "Could not load friends.";
              throw new Error(message);
            }
            followingForGifts = data;
            followingForGiftsLoad = null;
            return data;
          })
          .catch((err) => {
            followingForGiftsLoad = null;
            throw err;
          });
      }
      return followingForGiftsLoad;
    }

    function postGifts(path, body) {
      return fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          const message = data && typeof data.error === "string" && data.error.trim()
            ? data.error.trim()
            : "That list could not be saved.";
          throw new Error(message);
        }
        return takeGifts(data);
      });
    }

    function paintGiftFaces(section, slug, payload, following) {
      const list = section.querySelector(".gift-faces");
      const empty = section.querySelector(".friend-empty");
      const noteLabel = section.querySelector(".gift-note-label");
      const status = section.querySelector(".gift-status");
      if (status) {
        status.hidden = true;
        status.textContent = "";
        status.classList.remove("is-error");
      }
      const skip = excludedUsernames(payload);
      const gifted = giftedUsernames(slug);
      const members = following && Array.isArray(following.members) ? following.members : [];
      const seen = new Set();
      const eligible = [];
      members.forEach((member) => {
        const username = String((member && member.username) || "");
        const key = username.toLowerCase();
        if (!username || seen.has(key) || skip.has(key)) return;
        seen.add(key);
        eligible.push(member);
      });
      if (list) list.replaceChildren();
      eligible.forEach((member) => {
        const username = String(member.username);
        const name = typeof member.name === "string" && member.name.trim()
          ? member.name.trim()
          : username;
        const onList = gifted.has(username.toLowerCase());
        const item = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = onList ? "gift-face is-gifted" : "gift-face";
        button.setAttribute("data-username", username);
        button.setAttribute("aria-pressed", onList ? "true" : "false");
        button.setAttribute(
          "aria-label",
          onList ? "On " + name + "'s list. Remove." : "Add for " + name
        );
        const avatarUrl = allowedUrl(member.avatar, [
          "https://a.ltrbxd.com/",
          "https://s.ltrbxd.com/",
          "https://secure.gravatar.com/",
        ]);
        if (avatarUrl) {
          const image = document.createElement("img");
          image.className = "friend-avatar";
          image.src = avatarUrl;
          image.alt = "";
          image.width = 36;
          image.height = 36;
          button.appendChild(image);
        } else {
          const fallback = document.createElement("span");
          fallback.className = "gift-face-fallback";
          fallback.setAttribute("aria-hidden", "true");
          fallback.textContent = (name.charAt(0) || "?").toUpperCase();
          button.appendChild(fallback);
        }
        const label = document.createElement("span");
        label.className = "gift-face-name";
        label.textContent = name;
        button.appendChild(label);
        item.appendChild(button);
        if (list) list.appendChild(item);
      });
      const hasFaces = eligible.length > 0;
      if (list) list.hidden = !hasFaces;
      if (noteLabel) noteLabel.hidden = !hasFaces;
      if (empty) empty.hidden = hasFaces;
    }

    function renderGiftPicker(panel, slug, payload) {
      const section = giftSection(panel);
      if (!section) return;
      const status = section.querySelector(".gift-status");
      const noteLabel = section.querySelector(".gift-note-label");
      section.hidden = false;
      if (section.dataset.giftReady !== "1" && status && !status.classList.contains("is-error")) {
        status.hidden = false;
        status.textContent = "Loading friends…";
      }
      const generation = String(Date.now()) + Math.random();
      section.dataset.giftGeneration = generation;
      Promise.all([ensureFollowingForGifts(), ensureGifts()])
        .then(([following]) => {
          if (!panel.isConnected || section.dataset.giftGeneration !== generation) return;
          paintGiftFaces(section, slug, payload, following);
          section.dataset.giftReady = "1";
        })
        .catch((err) => {
          if (!panel.isConnected || section.dataset.giftGeneration !== generation) return;
          if (status) {
            status.hidden = false;
            status.classList.add("is-error");
            status.textContent = err && err.message ? err.message : "Could not load friends.";
          }
          if (noteLabel) noteLabel.hidden = true;
        });
    }

    function showGiftStatus(status, message) {
      if (!status) return;
      status.hidden = false;
      status.classList.add("is-error");
      status.textContent = message;
    }

    function toggleGift(button) {
      const section = button.closest('[data-friend-section="gift"]');
      const panel = button.closest("[data-friend-slug]");
      if (!section || !panel || button.disabled) return;
      const slug = panel.getAttribute("data-friend-slug") || "";
      const to = button.getAttribute("data-username") || "";
      const status = section.querySelector(".gift-status");
      const onList = button.getAttribute("aria-pressed") === "true";
      if (!onList) {
        const facts = filmFacts(panel);
        if (!facts.title) {
          showGiftStatus(status, "That film needs a title.");
          return;
        }
        button.disabled = true;
        const noteField = section.querySelector(".gift-note");
        const body = {
          to,
          slug,
          title: facts.title,
          note: noteField ? noteField.value : "",
          poster: facts.poster,
        };
        if (facts.year) body.year = facts.year;
        postGifts("/api/gifts", body)
          .then(() => {
            if (noteField) noteField.value = "";
          })
          .catch((err) => {
            showGiftStatus(status, err && err.message ? err.message : "That list could not be saved.");
          })
          .finally(() => {
            button.disabled = false;
          });
        return;
      }
      button.disabled = true;
      postGifts("/api/gifts/remove", { to, slug })
        .catch((err) => {
          showGiftStatus(status, err && err.message ? err.message : "That film could not be removed.");
        })
        .finally(() => {
          button.disabled = false;
        });
    }

    function removeGiftCard(button) {
      if (button.disabled) return;
      const to = button.getAttribute("data-to") || "";
      const slug = button.getAttribute("data-slug") || "";
      button.disabled = true;
      postGifts("/api/gifts/remove", { to, slug }).catch((err) => {
        button.disabled = false;
        const status = document.getElementById("gift-board-status");
        showGiftStatus(status, err && err.message ? err.message : "That film could not be removed.");
      });
    }

    function ratingBadge(scoreText, styleText) {
      if (typeof scoreText !== "string" || !/^\d(?:\.\d)?$/.test(scoreText)) return null;
      const badge = document.createElement("span");
      badge.className = "rating-badge";
      badge.textContent = scoreText;
      if (
        typeof styleText === "string"
        && /^color: rgb\(\d{1,3},\d{1,3},\d{1,3}\); background: rgba\(\d{1,3},\d{1,3},\d{1,3},[\d.]+\);$/.test(styleText)
      ) {
        badge.style.cssText = styleText;
      }
      return badge;
    }

    function appendGiftCard(list, film, group, voice) {
      if (!film || typeof film.title !== "string") return;
      const item = document.createElement("li");
      item.className = "gift-card";
      item.setAttribute("data-gift-card", "");
      const note = typeof film.note === "string" ? film.note : "";
      const friendName = typeof group.name === "string" && group.name.trim()
        ? group.name.trim()
        : String(group.username || "");
      item.setAttribute("data-search", (film.title + " " + note + " " + friendName).toLowerCase());
      const posterUrl = allowedUrl(film.poster, ["https://a.ltrbxd.com/resized/"]);
      if (posterUrl) {
        const image = document.createElement("img");
        image.className = "gift-card-poster";
        image.src = posterUrl;
        image.alt = "";
        item.appendChild(image);
      }
      const body = document.createElement("div");
      body.className = "gift-card-body";
      const titleRow = document.createElement("p");
      titleRow.className = "gift-card-title-row";
      const filmUrl = allowedUrl(film.filmUrl, ["https://letterboxd.com/film/"]);
      const title = document.createElement(filmUrl ? "a" : "span");
      title.className = "gift-card-title";
      title.textContent = film.title;
      if (filmUrl) {
        title.href = filmUrl;
        title.target = "_blank";
        title.rel = "noopener noreferrer";
      }
      titleRow.appendChild(title);
      if (typeof film.year === "number") {
        const year = document.createElement("span");
        year.className = "gift-card-year";
        year.textContent = String(film.year);
        titleRow.appendChild(year);
      }
      body.appendChild(titleRow);
      if (note.trim()) {
        const line = document.createElement("p");
        line.className = "gift-card-note";
        line.textContent = note.trim();
        body.appendChild(line);
      }
      const meta = document.createElement("div");
      meta.className = "gift-card-meta";
      if (film.watched) {
        const watched = document.createElement("span");
        watched.className = "gift-watched";
        const badge = ratingBadge(film.watchedRatingText, film.watchedRatingStyle);
        watched.textContent = voice === "you"
          ? (badge ? "You rated it" : "You watched it")
          : (badge ? "They rated it" : "They watched it");
        if (badge) watched.appendChild(badge);
        meta.appendChild(watched);
      }
      if (typeof film.nonstopLabel === "string" && film.nonstopLabel.trim()) {
        const link = document.createElement("a");
        link.className = "nonstop-badge";
        link.href = "program-ranked.html#list";
        link.textContent = "Nonstop · " + film.nonstopLabel.trim();
        meta.appendChild(link);
      }
      const offers = Array.isArray(film.offers) ? film.offers : [];
      if (offers.length) {
        const row = document.createElement("div");
        row.className = "stream-offers";
        offers.forEach((offer) => {
          if (!offer || typeof offer.name !== "string" || typeof offer.url !== "string") return;
          const url = allowedUrl(offer.url, ["https://"]);
          if (!url) return;
          const anchor = document.createElement("a");
          const icon = allowedUrl(offer.icon, ["https://images.justwatch.com/"]);
          const kind = typeof offer.kind === "string" && offer.kind ? offer.kind : "";
          const label = kind ? offer.name + ", " + kind : offer.name;
          anchor.href = url;
          anchor.target = "_blank";
          anchor.rel = "noopener noreferrer";
          anchor.title = label;
          anchor.setAttribute("aria-label", label);
          if (icon) {
            anchor.className = "stream-offer";
            const image = document.createElement("img");
            image.src = icon;
            image.alt = "";
            anchor.appendChild(image);
          } else {
            anchor.className = "stream-offer stream-offer-text";
            anchor.textContent = offer.name;
          }
          row.appendChild(anchor);
        });
        if (row.childElementCount) meta.appendChild(row);
      }
      if (meta.childElementCount) body.appendChild(meta);
      item.appendChild(body);
      if (voice === "they") {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "gift-remove";
        remove.textContent = "Remove";
        remove.setAttribute("data-to", String(group.username || ""));
        remove.setAttribute("data-slug", String(film.slug || ""));
        remove.setAttribute("aria-label", "Remove " + film.title + " from " + friendName + "'s list");
        item.appendChild(remove);
      }
      list.appendChild(item);
    }

    function giftBlock(title, groups, voice) {
      const block = document.createElement("section");
      block.className = "gift-block";
      block.setAttribute("data-gift-block", voice === "they" ? "outgoing" : "incoming");
      const heading = document.createElement("h2");
      heading.className = "gift-block-title";
      heading.textContent = title;
      block.appendChild(heading);
      groups.forEach((group) => {
        if (!group) return;
        const person = document.createElement("section");
        person.className = "gift-person";
        person.setAttribute("data-gift-person", "");
        const head = document.createElement("h3");
        head.className = "gift-person-head";
        const name = typeof group.name === "string" && group.name.trim()
          ? group.name.trim()
          : String(group.username || "");
        const avatarUrl = allowedUrl(group.avatar, [
          "https://a.ltrbxd.com/",
          "https://s.ltrbxd.com/",
          "https://secure.gravatar.com/",
        ]);
        if (avatarUrl) {
          const image = document.createElement("img");
          image.className = "friend-avatar";
          image.src = avatarUrl;
          image.alt = "";
          image.width = 32;
          image.height = 32;
          head.appendChild(image);
        }
        const profile = allowedUrl(group.profile, ["https://letterboxd.com/"]);
        const label = document.createElement(profile ? "a" : "span");
        label.className = "gift-person-name";
        label.textContent = name;
        if (profile) {
          label.href = profile;
          label.target = "_blank";
          label.rel = "noopener noreferrer";
        }
        head.appendChild(label);
        person.appendChild(head);
        const cards = document.createElement("ul");
        cards.className = "gift-cards";
        (Array.isArray(group.films) ? group.films : []).forEach((film) => {
          appendGiftCard(cards, film, group, voice);
        });
        person.appendChild(cards);
        block.appendChild(person);
      });
      return block;
    }

    function renderGiftBoard(payload) {
      const board = document.getElementById("gift-board");
      const loading = document.getElementById("gift-board-status");
      if (!board) return;
      if (loading) {
        loading.hidden = true;
        loading.textContent = "";
        loading.classList.remove("is-error");
      }
      board.replaceChildren();
      const incoming = Array.isArray(payload.incoming) ? payload.incoming : [];
      const outgoing = Array.isArray(payload.outgoing) ? payload.outgoing : [];
      if (!incoming.length && !outgoing.length) {
        const empty = document.createElement("p");
        empty.className = "stream-note";
        empty.textContent = "No films yet. Open a film and add it for someone you follow.";
        board.appendChild(empty);
        applySearch();
        return;
      }
      if (incoming.length) {
        board.appendChild(giftBlock("Your friends think you'd love", incoming, "you"));
      }
      if (outgoing.length) {
        board.appendChild(giftBlock("For your friends", outgoing, "they"));
      }
      applySearch();
    }

    function markFriendError(slug, message) {
      let text =
        typeof message === "string" && message.trim()
          ? message.trim()
          : "Friend ratings could not be loaded.";
      if (text === "Failed to fetch") {
        text = "Letterboxd could not be reached. Try again in a moment.";
      }
      friendPanels(slug).forEach((panel) => {
        if (panel.dataset.friendState === "ready") return;
        const status = panel.querySelector(".friend-status");
        if (status) {
          status.hidden = false;
          status.textContent = text;
        }
        panel.querySelectorAll(".friend-list").forEach((list) => {
          list.hidden = true;
        });
        panel.querySelectorAll(".friend-group").forEach((group) => {
          group.hidden = true;
        });
      });
    }

    const backdropLoads = new Map();

    function detailFilmSlug(detail) {
      const marked = detail.querySelector("[data-backdrop-slug]");
      if (marked) return marked.getAttribute("data-backdrop-slug") || "";
      const friend = detail.querySelector("[data-friend-slug]");
      if (friend) return friend.getAttribute("data-friend-slug") || "";
      const link = detail.querySelector('a[href*="letterboxd.com/film/"]');
      if (!link) return "";
      const match = String(link.getAttribute("href") || "").match(/letterboxd\.com\/film\/([^/?#]+)/i);
      return match ? match[1] : "";
    }

    function showBackdrop(detail, url) {
      const safe = allowedUrl(url, ["https://a.ltrbxd.com/"]);
      if (!safe) return;
      let hero = detail.querySelector(".detail-hero");
      const main = detail.querySelector(".detail-main");
      if (!hero) {
        hero = document.createElement("div");
        hero.className = "detail-hero";
        detail.insertBefore(hero, main || detail.firstChild);
      }
      let image = hero.querySelector(".detail-backdrop");
      if (!image) {
        image = document.createElement("img");
        image.className = "detail-backdrop";
        image.alt = "";
        hero.prepend(image);
      }
      if (image.getAttribute("src") !== safe) image.src = safe;
      hero.hidden = false;
    }

    function loadBackdrop(detail) {
      if (!detail || detail.querySelector(".detail-backdrop")) return;
      const slug = detailFilmSlug(detail);
      if (!slug) return;
      let pending = backdropLoads.get(slug);
      if (!pending) {
        pending = fetch("/api/letterboxd-backdrop?slug=" + encodeURIComponent(slug))
          .then((response) => (response.ok ? response.json() : null))
          .then((data) => (data && typeof data.url === "string" ? data.url : ""))
          .catch(() => "");
        backdropLoads.set(slug, pending);
      }
      pending.then((url) => {
        if (!url || !detail.isConnected) return;
        showBackdrop(detail, url);
      });
    }

    function loadFriends(root, priority) {
      if (!root) return;
      const signedIn = viewerIsSignedIn();
      root.querySelectorAll("[data-friend-slug]").forEach((panel) => {
        const slug = panel.getAttribute("data-friend-slug");
        if (!signedIn) {
          panel.hidden = true;
          return;
        }
        if (!slug || panel.dataset.friendState === "ready") return;
        startFriendLoad(slug, priority !== false);
      });
    }

    function uniqueFriendSlugs() {
      const slugs = [];
      const seen = new Set();
      document.querySelectorAll("[data-friend-slug]").forEach((panel) => {
        const slug = panel.getAttribute("data-friend-slug");
        if (!slug || seen.has(slug)) return;
        seen.add(slug);
        slugs.push(slug);
      });
      return slugs;
    }

    function prefetchFriendRatings() {
      if (!viewerIsSignedIn()) {
        document.querySelectorAll("[data-friend-slug]").forEach((panel) => {
          panel.hidden = true;
        });
        return;
      }
      uniqueFriendSlugs().forEach((slug) => startFriendLoad(slug, false));
    }

    function sortValue(row, key, type) {
      const raw = row.getAttribute("data-sort-" + key) || "";
      if (type === "number") {
        if (raw === "") return null;
        const value = Number(raw);
        return Number.isFinite(value) ? value : null;
      }
      return raw;
    }

    function compareRows(a, b, key, type, direction) {
      const left = sortValue(a, key, type);
      const right = sortValue(b, key, type);
      const leftMissing = left === null || left === "";
      const rightMissing = right === null || right === "";
      if (leftMissing && rightMissing) return 0;
      if (leftMissing) return 1;
      if (rightMissing) return -1;
      const cmp = type === "number"
        ? left - right
        : String(left).localeCompare(String(right), undefined, { sensitivity: "base", numeric: true });
      return direction === "asc" ? cmp : -cmp;
    }

    function bindTableSorting(table) {
      if (table.dataset.gmfBound) return;
      table.dataset.gmfBound = "1";
      const tbody = table.querySelector("tbody");
      if (!tbody) return;
      table.querySelectorAll("button.sort").forEach((button) => {
        button.addEventListener("click", () => {
          const header = button.closest("th");
          const key = button.dataset.sortKey;
          const type = button.dataset.sortType || "text";
          const current = header.getAttribute("aria-sort");
          const direction = current === "descending"
            ? "asc"
            : current === "ascending"
              ? "desc"
              : (type === "number" ? "desc" : "asc");
          table.querySelectorAll("th").forEach((cell) => cell.removeAttribute("aria-sort"));
          header.setAttribute("aria-sort", direction === "asc" ? "ascending" : "descending");
          const pairs = [...tbody.querySelectorAll("[data-movie-row], [data-stream-row]")].map((row) => {
            const detail = row.nextElementSibling;
            const isDetail = detail && (
              detail.hasAttribute("data-movie-detail") || detail.hasAttribute("data-stream-detail")
            );
            return [row, isDetail ? detail : null];
          });
          pairs.sort((a, b) => compareRows(a[0], b[0], key, type, direction));
          for (const [row, detail] of pairs) {
            tbody.appendChild(row);
            if (detail) tbody.appendChild(detail);
          }
        });
      });
    }

    document.querySelectorAll("table").forEach(bindTableSorting);

    function bindRecommendationsPanel(root) {
      const panel = root || document.getElementById("recommendations-view");
      if (!panel) return;
      panel.querySelectorAll(".program-section").forEach(bindProgramSection);
      panel.querySelectorAll("[data-movie-row]").forEach(bindMovieRow);
      panel.querySelectorAll("table").forEach(bindTableSorting);
    }

    function recommendationsEndpoint() {
      const node = document.getElementById("viewer-config");
      let port = 8765;
      try {
        const parsed = JSON.parse((node && node.textContent) || "{}");
        if (parsed && typeof parsed.port === "number") port = parsed.port;
      } catch (err) {
        /* static page */
      }
      if (location.protocol === "file:") {
        return "http://127.0.0.1:" + port + "/api/recommendations";
      }
      return "/api/recommendations";
    }

    function applyRecommendationsPayload(payload) {
      if (!payload || typeof payload.panel !== "string") return;
      const panel = document.getElementById("recommendations-view");
      if (!panel) return;
      panel.innerHTML = payload.panel;
      bindRecommendationsPanel(panel);
      const nav = document.getElementById("recommendations-nav");
      if (nav && typeof payload.pills === "string") {
        nav.innerHTML = '<span class="nav-pills-label">Jump to</span>' + payload.pills;
      }
      const updated = document.getElementById("taste-updated");
      if (updated) {
        const label = typeof payload.updatedLabel === "string" ? payload.updatedLabel : "";
        updated.textContent = label;
        updated.hidden = !label;
      }
      applySearch();
      updateScrolledSection();
    }

    let tastePollTimer = null;

    function stopTastePoll() {
      if (tastePollTimer) {
        clearTimeout(tastePollTimer);
        tastePollTimer = null;
      }
    }

    async function pollRecommendationsRefresh(statusEl, button) {
      try {
        const response = await fetch(recommendationsEndpoint());
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.error || "Could not refresh recommendations.");
        }
        if (payload.status === "running") {
          tastePollTimer = setTimeout(() => pollRecommendationsRefresh(statusEl, button), 2000);
          return;
        }
        if (payload.error) {
          if (statusEl) {
            statusEl.hidden = false;
            statusEl.classList.add("is-error");
            statusEl.textContent = payload.error;
          }
        } else if (statusEl) {
          statusEl.textContent = "";
          statusEl.hidden = true;
          statusEl.classList.remove("is-error");
        }
        applyRecommendationsPayload(payload);
      } catch (err) {
        if (statusEl) {
          statusEl.hidden = false;
          statusEl.classList.add("is-error");
          statusEl.textContent = err && err.message ? err.message : "Could not refresh recommendations.";
        }
      } finally {
        if (!tastePollTimer && button) button.disabled = false;
      }
    }

    async function refreshRecommendations() {
      const button = document.getElementById("taste-refresh");
      const statusEl = document.getElementById("taste-refresh-status");
      if (!button) return;
      stopTastePoll();
      button.disabled = true;
      if (statusEl) {
        statusEl.hidden = false;
        statusEl.classList.remove("is-error");
        statusEl.textContent = "Updating your ratings and recommendations…";
      }
      try {
        const response = await fetch(recommendationsEndpoint(), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const fallback = response.status === 404
            ? "Restart python3 goodmoviefinder.py --serve, then refresh recommendations."
            : "Could not refresh recommendations.";
          throw new Error(payload.error || fallback);
        }
        tastePollTimer = setTimeout(() => pollRecommendationsRefresh(statusEl, button), 1500);
      } catch (err) {
        if (statusEl) {
          statusEl.hidden = false;
          statusEl.classList.add("is-error");
          statusEl.textContent = err && err.message ? err.message : "Could not refresh recommendations.";
        }
        button.disabled = false;
      }
    }

    const tasteRefresh = document.getElementById("taste-refresh");
    if (tasteRefresh) tasteRefresh.addEventListener("click", refreshRecommendations);

    let programPollTimer = null;
    let programReloading = false;

    function stopProgramPoll() {
      if (programPollTimer) {
        clearTimeout(programPollTimer);
        programPollTimer = null;
      }
    }

    async function pollProgramCheck(statusEl, button) {
      programPollTimer = null;
      try {
        const response = await fetch(matchApi("/api/program"), { cache: "no-store" });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.error || "Could not check the Wien program.");
        }
        if (payload.status === "running") {
          if (statusEl && payload.message) statusEl.textContent = payload.message;
          programPollTimer = setTimeout(() => pollProgramCheck(statusEl, button), 1500);
          return;
        }
        if (payload.reload) {
          programReloading = true;
          if (statusEl) {
            statusEl.textContent = (payload.message || "Program updated.") + " Reloading…";
          }
          window.location.reload();
          return;
        }
        if (statusEl) {
          statusEl.hidden = false;
          statusEl.classList.toggle("is-error", Boolean(payload.error));
          statusEl.textContent = payload.error || payload.message || "The Wien program is unchanged.";
        }
      } catch (err) {
        if (statusEl) {
          statusEl.hidden = false;
          statusEl.classList.add("is-error");
          statusEl.textContent = err && err.message ? err.message : "Could not check the Wien program.";
        }
      } finally {
        if (!programPollTimer && !programReloading && button) button.disabled = false;
      }
    }

    async function checkForNewFilms() {
      const button = document.getElementById("program-check");
      const statusEl = document.getElementById("program-check-status");
      if (!button || button.disabled) return;
      stopProgramPoll();
      button.disabled = true;
      if (statusEl) {
        statusEl.hidden = false;
        statusEl.classList.remove("is-error");
        statusEl.textContent = "Reading the Wien program…";
      }
      try {
        const response = await fetch(matchApi("/api/program"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const fallback = response.status === 404 || response.status === 503
            ? "Start python3 goodmoviefinder.py --serve, then check for new films."
            : "Could not check the Wien program.";
          throw new Error(payload.error || fallback);
        }
        if (statusEl && payload.message) statusEl.textContent = payload.message;
        programPollTimer = setTimeout(() => pollProgramCheck(statusEl, button), 800);
      } catch (err) {
        if (statusEl) {
          statusEl.hidden = false;
          statusEl.classList.add("is-error");
          const offline = err && err.message && /failed to fetch|networkerror|load failed/i.test(err.message);
          statusEl.textContent = offline
            ? "Start python3 goodmoviefinder.py --serve, then check for new films."
            : (err && err.message ? err.message : "Could not check the Wien program.");
        }
        button.disabled = false;
      }
    }

    const programCheck = document.getElementById("program-check");
    if (programCheck) programCheck.addEventListener("click", checkForNewFilms);

    function matchApi(path) {
      const node = document.getElementById("viewer-config");
      let port = 8765;
      try {
        const parsed = JSON.parse((node && node.textContent) || "{}");
        if (parsed && typeof parsed.port === "number") port = parsed.port;
      } catch (err) {
        /* static page */
      }
      if (location.protocol === "file:") {
        return "http://127.0.0.1:" + port + path;
      }
      return path;
    }

    function matchRoot(node) {
      return node && node.closest(".match-repair");
    }

    function setMatchStatus(root, message, isError) {
      const status = root.querySelector(".match-status");
      if (!status) return;
      status.hidden = !message;
      status.textContent = message || "";
      status.classList.toggle("is-error", !!isError);
    }

    function renderMatchResults(root, films) {
      const list = root.querySelector(".match-results");
      if (!list) return;
      list.replaceChildren();
      if (!films.length) {
        list.hidden = true;
        return;
      }
      films.forEach((film) => {
        if (!film || typeof film.slug !== "string") return;
        const item = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "match-pick";
        button.setAttribute("data-slug", film.slug);
        const title = document.createElement("span");
        title.className = "match-pick-title";
        title.textContent = typeof film.title === "string" ? film.title : film.slug;
        button.appendChild(title);
        const bits = [];
        if (typeof film.year === "number") bits.push(String(film.year));
        if (Array.isArray(film.directors) && film.directors.length) {
          const names = film.directors.filter((name) => typeof name === "string" && name.trim());
          bits.push(names.length > 2 ? names.slice(0, 2).join(", ") + ", …" : names.join(", "));
        }
        if (bits.length) {
          const meta = document.createElement("span");
          meta.className = "match-pick-meta";
          meta.textContent = bits.join(" · ");
          button.appendChild(meta);
        }
        item.appendChild(button);
        list.appendChild(item);
      });
      list.hidden = list.childElementCount === 0;
    }

    async function searchLetterboxdMatch(root) {
      const input = root.querySelector(".match-query");
      const query = input && input.value ? input.value.trim() : "";
      if (!query) {
        setMatchStatus(root, "Type a title to search Letterboxd.", true);
        return;
      }
      const searchButton = root.querySelector(".match-search");
      if (searchButton) searchButton.disabled = true;
      setMatchStatus(root, "Searching Letterboxd…", false);
      renderMatchResults(root, []);
      try {
        const response = await fetch(
          matchApi("/api/letterboxd-search?q=" + encodeURIComponent(query))
        );
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const fallback = response.status === 404
            ? "Restart python3 goodmoviefinder.py --serve to fix the Letterboxd match."
            : "Letterboxd could not be reached.";
          throw new Error(payload.error || fallback);
        }
        const films = Array.isArray(payload.films) ? payload.films : [];
        if (!films.length) {
          setMatchStatus(root, "No Letterboxd films matched that search.", true);
          return;
        }
        setMatchStatus(root, "Pick the right film.", false);
        renderMatchResults(root, films);
      } catch (err) {
        setMatchStatus(
          root,
          err && err.message ? err.message : "Letterboxd could not be reached.",
          true
        );
      } finally {
        if (searchButton) searchButton.disabled = false;
      }
    }

    async function applyLetterboxdMatch(root, slug) {
      const url = root.getAttribute("data-match-url");
      if (!url || !slug) return;
      setMatchStatus(root, "Saving this Letterboxd page…", false);
      root.querySelectorAll(".match-pick, .match-search, .match-toggle").forEach((el) => {
        el.disabled = true;
      });
      try {
        const response = await fetch(matchApi("/api/match"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nonstopUrl: url, slug }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const fallback = response.status === 404
            ? "Restart python3 goodmoviefinder.py --serve to fix the Letterboxd match."
            : "That Letterboxd page could not be saved.";
          throw new Error(payload.error || fallback);
        }
        setMatchStatus(root, "Saved. Reloading…", false);
        window.location.reload();
      } catch (err) {
        setMatchStatus(
          root,
          err && err.message ? err.message : "That Letterboxd page could not be saved.",
          true
        );
        root.querySelectorAll(".match-pick, .match-search, .match-toggle").forEach((el) => {
          el.disabled = false;
        });
      }
    }

    document.addEventListener("click", (event) => {
      const toggle = event.target.closest(".match-toggle");
      if (toggle) {
        event.preventDefault();
        event.stopPropagation();
        const root = matchRoot(toggle);
        const form = root && root.querySelector(".match-form");
        if (!form) return;
        const open = form.hidden;
        form.hidden = !open;
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) {
          const input = form.querySelector(".match-query");
          if (input) input.focus();
        }
        return;
      }
      const pick = event.target.closest(".match-pick");
      if (pick) {
        event.preventDefault();
        event.stopPropagation();
        const root = matchRoot(pick);
        if (root) applyLetterboxdMatch(root, pick.getAttribute("data-slug") || "");
      }
    });

    document.addEventListener("submit", (event) => {
      const form = event.target.closest(".match-form");
      if (!form) return;
      event.preventDefault();
      event.stopPropagation();
      const root = matchRoot(form);
      if (root) searchLetterboxdMatch(root);
    });

    document.addEventListener("click", (event) => {
      const face = event.target.closest(".gift-face");
      if (face) {
        event.preventDefault();
        toggleGift(face);
        return;
      }
      const remove = event.target.closest(".gift-remove");
      if (!remove) return;
      event.preventDefault();
      removeGiftCard(remove);
    });

    if (document.getElementById("gift-board") && viewerIsSignedIn()) {
      ensureGifts().catch((err) => {
        const status = document.getElementById("gift-board-status");
        showGiftStatus(
          status,
          err && err.message ? err.message : "Those lists could not be loaded."
        );
      });
    }

    const startFriendPrefetch = () => prefetchFriendRatings();
    if (typeof requestIdleCallback === "function") {
      requestIdleCallback(startFriendPrefetch, { timeout: 800 });
    } else {
      setTimeout(startFriendPrefetch, 0);
    }
