    const search = document.getElementById("search");
    const searchCount = document.getElementById("search-count");
    const searchClear = document.getElementById("search-clear");
    const searchField = document.querySelector(".search-field");
    const listEmpty = document.getElementById("list-empty");
    const calendarEmpty = document.getElementById("calendar-empty");
    let sectionFilter = null;
    let scrolledSection = null;

    function isCalendar() {
      const board = document.getElementById("calendar-view");
      return !!board && !board.hidden;
    }

    function isPlan() {
      const plan = document.getElementById("plan-view");
      return !!plan && !plan.hidden;
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
      if (isCalendar() || isPlan()) return;
      const marker = (document.querySelector(".toolbar")?.getBoundingClientRect().bottom || 0) + 16;
      const sections = [...document.querySelectorAll(".program-section")].filter((section) => !section.hidden);
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
        if (!el.hidden) ids.add(el.getAttribute("data-movie-id"));
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
      const narrowing = searching || (isCalendar() && !!sectionFilter);
      document.querySelectorAll(".day-film").forEach((film) => {
        const blob = film.getAttribute("data-search") || "";
        const langHide = film.getAttribute("data-lang-hide") === "1";
        const sectionMiss = isCalendar() && sectionFilter && film.getAttribute("data-section") !== sectionFilter;
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
      const listMatches = visibleCount("[data-movie-row]");
      const dayMatches = visibleCount(".day-film");
      const activeMatches = isCalendar() ? dayMatches : listMatches;
      if (searchField) searchField.classList.toggle("is-active", searching);
      if (searchCount) {
        searchCount.hidden = !searching;
        searchCount.textContent = searching ? String(activeMatches) : "";
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

    function readFolded() {
      try {
        const raw = localStorage.getItem(FOLD_STORAGE);
        const ids = raw ? JSON.parse(raw) : [];
        return new Set(Array.isArray(ids) ? ids : []);
      } catch (err) {
        return new Set();
      }
    }

    function writeFolded(ids) {
      try {
        localStorage.setItem(FOLD_STORAGE, JSON.stringify([...ids]));
      } catch (err) {
        /* private mode or storage full */
      }
    }

    const foldedIds = readFolded();

    function sectionIsOpen(section) {
      return !section.classList.contains("is-folded") || section.classList.contains("is-search-open");
    }

    function syncSection(section) {
      const button = section.querySelector(".section-toggle");
      if (button) button.setAttribute("aria-expanded", sectionIsOpen(section) ? "true" : "false");
    }

    function setFolded(section, isFolded) {
      section.classList.toggle("is-folded", isFolded);
      if (isFolded) foldedIds.add(section.id);
      else foldedIds.delete(section.id);
      writeFolded(foldedIds);
      syncSection(section);
    }

    document.querySelectorAll(".program-section").forEach((section) => {
      if (foldedIds.has(section.id)) section.classList.add("is-folded");
      syncSection(section);
      const button = section.querySelector(".section-toggle");
      if (!button) return;
      button.addEventListener("click", () => {
        setFolded(section, !section.classList.contains("is-folded"));
      });
    });

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
        if (isPlan()) setView("list");
        const section = document.getElementById(id);
        if (section && section.classList.contains("program-section")) setFolded(section, false);
      });
    });

    document.querySelectorAll("[data-movie-row]").forEach((row) => {
      row.addEventListener("click", (event) => {
        if (event.target.closest(".plan-add")) return;
        const button = row.querySelector(".expand");
        const detail = row.nextElementSibling;
        if (!button || !detail || !detail.hasAttribute("data-movie-detail")) return;
        const open = button.getAttribute("aria-expanded") !== "true";
        button.setAttribute("aria-expanded", open ? "true" : "false");
        row.classList.toggle("is-open", open);
        detail.hidden = !open;
      });
    });

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

    document.querySelectorAll("table").forEach((table) => {
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
          const pairs = [...tbody.querySelectorAll("[data-movie-row]")].map((row) => {
            const detail = row.nextElementSibling;
            return [row, detail && detail.hasAttribute("data-movie-detail") ? detail : null];
          });
          pairs.sort((a, b) => compareRows(a[0], b[0], key, type, direction));
          for (const [row, detail] of pairs) {
            tbody.appendChild(row);
            if (detail) tbody.appendChild(detail);
          }
        });
      });
    });

    const VIEW_STORAGE = "gmf-view";

    function setView(view) {
      if (view !== "list" && view !== "calendar" && view !== "plan") view = "list";
      const list = document.getElementById("list-view");
      const board = document.getElementById("calendar-view");
      const plan = document.getElementById("plan-view");
      if (list) list.hidden = view !== "list";
      if (board) board.hidden = view !== "calendar";
      if (plan) plan.hidden = view !== "plan";
      if (view !== "calendar") sectionFilter = null;
      document.querySelectorAll(".view-option").forEach((button) => {
        button.setAttribute("aria-pressed", button.dataset.view === view ? "true" : "false");
      });
      try {
        localStorage.setItem(VIEW_STORAGE, view);
      } catch (err) {
        /* private mode or storage full */
      }
      applySearch();
      if (view === "calendar") syncSectionLinks();
      else if (view === "plan") {
        document.querySelectorAll(".nav-pills a[aria-current]").forEach((link) => {
          link.removeAttribute("aria-current");
        });
        if (typeof renderPlan === "function") renderPlan();
      } else updateScrolledSection();
    }

    document.querySelectorAll(".view-option").forEach((button) => {
      button.addEventListener("click", () => setView(button.dataset.view));
    });
    document.querySelectorAll("[data-day]").forEach((button) => {
      button.addEventListener("click", () => toggleDay(button));
    });
    document.querySelectorAll(".day-film-title").forEach((button) => {
      button.addEventListener("click", () => toggleFilmDetail(button));
    });

    const AUDIO_VERSIONS = new Set(["OV", "OmdU", "OmeU"]);
    const VIEWER_STORAGE = "gmf-viewer";
    const viewerConfig = readViewerConfig();
    let serverReady = null;

    function readViewerConfig() {
      const node = document.getElementById("viewer-config");
      try {
        const parsed = JSON.parse((node && node.textContent) || "{}");
        if (parsed && typeof parsed === "object") return parsed;
      } catch (err) {
        /* static page data */
      }
      return {
        letterboxdUser: "",
        languages: ["English", "French"],
        defaults: ["English", "French"],
        stamp: "",
        port: 8765,
      };
    }

    function viewerEndpoint() {
      if (location.protocol === "file:") {
        return "http://127.0.0.1:" + (viewerConfig.port || 8765) + "/api/viewer";
      }
      return "/api/viewer";
    }

    function selectedLanguageNames() {
      return [...document.querySelectorAll('#settings-panel input[name="language"]:checked')].map(
        (box) => box.value
      );
    }

    function knownLanguageSet() {
      return new Set(selectedLanguageNames().map((name) => name.toLowerCase()));
    }

    function versionFollowable(primary, version) {
      const language = (primary || "").trim().toLowerCase();
      if (!language) return true;
      if (knownLanguageSet().has(language)) return AUDIO_VERSIONS.has(version);
      return version === "OmeU";
    }

    function filmFollowable(primary, versions) {
      const language = (primary || "").trim().toLowerCase();
      if (!language) return true;
      return versions.some((version) => versionFollowable(primary, version));
    }

    window.gmfViewer = {
      versionFollowable,
    };

    function languageBlurb(names) {
      let phrase;
      if (!names.length) phrase = "English subtitles";
      else if (names.length === 1) phrase = names[0] + " original, or English subtitles";
      else if (names.length === 2) phrase = names[0] + " or " + names[1] + " original, or English subtitles";
      else phrase = names.slice(0, -1).join(", ") + ", or " + names[names.length - 1] + " original, or English subtitles";
      return phrase + " — sorted by Letterboxd rating";
    }

    function applyCheckboxSelection(names) {
      const wanted = new Set(names.map((name) => String(name).toLowerCase()));
      document.querySelectorAll('#settings-panel input[name="language"]').forEach((box) => {
        box.checked = wanted.has(box.value.toLowerCase());
      });
    }

    function readSavedLanguages(stamp) {
      try {
        const raw = localStorage.getItem(VIEWER_STORAGE);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || parsed.stamp !== stamp || !Array.isArray(parsed.languages)) return null;
        return parsed.languages.filter((name) => typeof name === "string");
      } catch (err) {
        return null;
      }
    }

    function persistLanguages() {
      const names = selectedLanguageNames();
      try {
        localStorage.setItem(
          VIEWER_STORAGE,
          JSON.stringify({ stamp: viewerConfig.stamp, languages: names })
        );
      } catch (err) {
        /* private mode or storage full */
      }
      if (serverReady === false) return;
      fetch(viewerEndpoint(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ languages: names }),
      }).then((response) => {
        serverReady = response.ok;
      }).catch(() => {
        serverReady = false;
      });
    }

    function syncSchedule(root, primary) {
      root.querySelectorAll("li[data-version]").forEach((item) => {
        item.hidden = !versionFollowable(primary, item.getAttribute("data-version") || "");
      });
      root.querySelectorAll(".showtime-day").forEach((day) => {
        const any = [...day.querySelectorAll("li[data-version]")].some((item) => !item.hidden);
        day.hidden = !any;
      });
      const schedule = root.querySelector("[data-schedule]");
      const empty = root.querySelector(".schedule-empty");
      if (schedule && empty) {
        const any = [...schedule.querySelectorAll("li[data-version]")].some((item) => !item.hidden);
        schedule.hidden = !any;
        empty.hidden = any;
      }
      root.querySelectorAll(".cal-day[data-screenings]").forEach((cell) => {
        let items = [];
        try {
          items = JSON.parse(cell.getAttribute("data-screenings") || "[]");
        } catch (err) {
          items = [];
        }
        const follow = items.filter((item) => versionFollowable(primary, item.v || ""));
        cell.classList.toggle("day-mark", follow.length > 0);
        cell.title = follow.map((item) => item.t || "").filter(Boolean).join(", ");
      });
    }

    function refreshOpenFilmDetails() {
      document.querySelectorAll(".day-film-title[aria-expanded='true']").forEach((button) => {
        const film = button.closest(".day-film");
        if (!film || film.hidden) {
          button.setAttribute("aria-expanded", "false");
          const slot = film && film.querySelector(".day-film-detail");
          if (slot) {
            slot.hidden = true;
            slot.replaceChildren();
          }
          return;
        }
        fillFilmDetail(film);
      });
    }

    function updateSections() {
      document.querySelectorAll(".program-section").forEach((section) => {
        const rows = [...section.querySelectorAll("[data-movie-row]")];
        const shown = rows.filter((row) => row.getAttribute("data-lang-hide") !== "1");
        section.hidden = shown.length === 0;
        const count = section.querySelector("[data-section-count]");
        if (count) {
          const total = shown.length;
          count.textContent = total + " " + (total === 1 ? "film" : "films");
        }
        const link = document.querySelector('.nav-pills a[data-nav="' + section.id + '"]');
        if (link) link.hidden = shown.length === 0;
      });
      const note = document.getElementById("program-language-note");
      if (note) note.textContent = languageBlurb(selectedLanguageNames());
      if (sectionFilter) {
        const section = document.getElementById(sectionFilter);
        if (!section || section.hidden) sectionFilter = null;
      }
    }

    function applyLanguageFilter() {
      document.querySelectorAll("[data-movie-row]").forEach((row) => {
        const primary = row.getAttribute("data-primary-language") || "";
        const versions = (row.getAttribute("data-versions") || "").split(/\s+/).filter(Boolean);
        const follow = filmFollowable(primary, versions);
        const hideUnfollowable = row.getAttribute("data-filter") === "hide";
        row.setAttribute("data-lang-hide", hideUnfollowable && !follow ? "1" : "0");
        row.classList.toggle("row-no-follow", !follow && !hideUnfollowable);
        if (!row.hasAttribute("data-search-base")) {
          row.setAttribute("data-search-base", row.getAttribute("data-search") || "");
        }
        const phrase = !follow && !hideUnfollowable ? " no followable version" : "";
        row.setAttribute("data-search", (row.getAttribute("data-search-base") || "") + phrase);
        const badge = row.querySelector(".follow-badge");
        if (badge) badge.hidden = follow || hideUnfollowable;
        const detail = row.nextElementSibling;
        if (detail && detail.hasAttribute("data-movie-detail")) syncSchedule(detail, primary);
      });
      document.querySelectorAll(".day-film").forEach((film) => {
        const primary = film.getAttribute("data-primary-language") || "";
        const items = [...film.querySelectorAll("li[data-version]")];
        items.forEach((item) => {
          item.hidden = !versionFollowable(primary, item.getAttribute("data-version") || "");
        });
        const visible = items.filter((item) => !item.hidden);
        film.setAttribute("data-lang-hide", visible.length ? "0" : "1");
        const line = film.querySelector(".day-times-line");
        if (line) {
          line.textContent = visible
            .map((item) => item.getAttribute("data-clock") || "")
            .filter(Boolean)
            .sort()
            .join(" · ");
        }
      });
      updateSections();
      applySearch();
      refreshOpenFilmDetails();
      if (isCalendar() || isPlan()) syncSectionLinks();
      else updateScrolledSection();
      if (typeof renderPlan === "function") renderPlan();
    }

    function setSettingsOpen(open) {
      const panel = document.getElementById("settings-panel");
      const button = document.getElementById("settings-open");
      if (!panel || !button) return;
      panel.hidden = !open;
      button.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        const input = document.getElementById("letterboxd-user");
        if (input) input.focus();
      }
    }

    function bindSettings() {
      const openButton = document.getElementById("settings-open");
      const form = document.getElementById("settings-form");
      const reset = document.getElementById("settings-reset");
      if (openButton) {
        openButton.addEventListener("click", () => {
          const panel = document.getElementById("settings-panel");
          setSettingsOpen(!panel || panel.hidden);
        });
      }
      document.querySelectorAll('#settings-panel input[name="language"]').forEach((box) => {
        box.addEventListener("change", () => {
          persistLanguages();
          applyLanguageFilter();
        });
      });
      if (reset) {
        reset.addEventListener("click", () => {
          applyCheckboxSelection(viewerConfig.defaults || ["English", "French"]);
          persistLanguages();
          applyLanguageFilter();
        });
      }
      if (form) form.addEventListener("submit", loadProfile);
      document.addEventListener("click", (event) => {
        const panel = document.getElementById("settings-panel");
        if (!panel || panel.hidden) return;
        if (event.target.closest(".settings")) return;
        setSettingsOpen(false);
      });
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        const panel = document.getElementById("settings-panel");
        if (!panel || panel.hidden) return;
        setSettingsOpen(false);
        if (openButton) openButton.focus();
      });
    }

    async function loadProfile(event) {
      event.preventDefault();
      const input = document.getElementById("letterboxd-user");
      const status = document.getElementById("settings-status");
      const button = event.target.querySelector(".settings-load");
      const user = (input && input.value ? input.value : "").trim().replace(/^@/, "");
      if (!user) {
        if (status) status.textContent = "Enter a Letterboxd username.";
        return;
      }
      if (user.toLowerCase() === String(viewerConfig.letterboxdUser || "").toLowerCase()) {
        if (status) status.textContent = "This page already uses that profile.";
        return;
      }
      if (status) status.textContent = "Loading profile…";
      if (button) button.disabled = true;
      try {
        const response = await fetch(viewerEndpoint(), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ letterboxdUser: user, languages: selectedLanguageNames() }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          if (status) status.textContent = payload.error || "Could not load that profile.";
          return;
        }
        serverReady = true;
        if (payload.reload) window.location.reload();
        else if (status) status.textContent = "";
      } catch (err) {
        if (status) {
          status.textContent = "Run python3 goodmoviefinder.py --serve, then load the profile again.";
        }
      } finally {
        if (button) button.disabled = false;
      }
    }

    function bootViewer() {
      const saved = readSavedLanguages(viewerConfig.stamp);
      if (saved) applyCheckboxSelection(saved);
      applyLanguageFilter();
      bindSettings();
    }

    bootViewer();

    let storedView = "list";
    try {
      const saved = localStorage.getItem(VIEW_STORAGE);
      if (saved === "calendar" || saved === "plan") storedView = saved;
    } catch (err) {
      storedView = "list";
    }
    setView(storedView);

    search.addEventListener("input", applySearch);
    if (searchClear) {
      searchClear.addEventListener("click", () => {
        search.value = "";
        applySearch();
        search.focus();
      });
    }
    document.addEventListener("scroll", updateScrolledSection, { passive: true });
