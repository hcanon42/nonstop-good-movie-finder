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
      const listMatches = visibleCount("[data-movie-row]");
      const dayMatches = visibleCount(".day-film");
      const streamMatches = visibleCount("[data-stream-row]");
      const activeMatches = isCalendar() ? dayMatches : isWatchlist() ? streamMatches : listMatches;
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
      loadFriends(slot);
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

    function bindMovieRow(row) {
      if (row.dataset.gmfBound) return;
      row.dataset.gmfBound = "1";
      row.addEventListener("click", (event) => {
        if (event.target.closest(".plan-add")) return;
        const button = row.querySelector(".expand");
        const detail = row.nextElementSibling;
        if (!button || !detail || !detail.hasAttribute("data-movie-detail")) return;
        const open = button.getAttribute("aria-expanded") !== "true";
        button.setAttribute("aria-expanded", open ? "true" : "false");
        row.classList.toggle("is-open", open);
        detail.hidden = !open;
        if (open) loadFriends(detail);
      });
    }

    document.querySelectorAll("[data-movie-row]").forEach(bindMovieRow);

    document.querySelectorAll("[data-stream-row]").forEach((row) => {
      row.addEventListener("click", (event) => {
        if (event.target.closest("a")) return;
        const button = row.querySelector(".expand");
        const detail = row.nextElementSibling;
        if (!button || !detail || !detail.hasAttribute("data-stream-detail")) return;
        const open = button.getAttribute("aria-expanded") !== "true";
        button.setAttribute("aria-expanded", open ? "true" : "false");
        row.classList.toggle("is-open", open);
        detail.hidden = !open;
        if (open) loadFriends(detail);
      });
    });

    const friendLoads = new Map();
    let friendFetchChain = Promise.resolve();

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
      const job = friendFetchChain.then(() => attempt(tries));
      friendFetchChain = job.catch(() => {});
      return job;
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
      if (!friend || typeof friend.name !== "string" || !list) return;
      const item = document.createElement("li");
      item.className = "friend";
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
      if (options.showRating && typeof friend.comment === "string" && friend.comment.trim()) {
        const comment = document.createElement("p");
        comment.className = "friend-comment";
        comment.textContent = friend.comment;
        body.appendChild(comment);
      }
      item.appendChild(body);
      list.appendChild(item);
    }

    function renderFriendSection(section, friends, options) {
      if (!section || !section.list) return;
      section.list.replaceChildren();
      friends.forEach((friend) => appendFriendRow(section.list, friend, options));
      const hasFriends = friends.length > 0;
      section.list.hidden = !hasFriends;
      if (section.empty) section.empty.hidden = hasFriends;
      section.group.hidden = false;
    }

    function renderFriends(slug, payload) {
      const friends = Array.isArray(payload.friends) ? payload.friends : [];
      const watchlist = Array.isArray(payload.watchlist) ? payload.watchlist : [];
      friendPanels(slug).forEach((panel) => {
        const status = panel.querySelector(".friend-status");
        const rated = friendGroup(panel, "rated");
        const listed = friendGroup(panel, "watchlist");
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
              appendFriendRow(legacyList, friend, { showRating: true })
            );
            legacyList.hidden = false;
          }
          panel.dataset.friendState = "ready";
          return;
        }
        if (status) status.hidden = true;
        renderFriendSection(rated, friends, { showRating: true });
        renderFriendSection(listed, watchlist, { watchlistBadge: true });
        if (!friends.length && !watchlist.length && status) {
          status.hidden = false;
          status.textContent =
            "None of your Letterboxd friends have rated this or have it on their watchlist.";
        }
        panel.dataset.friendState = "ready";
      });
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

    function loadFriends(root) {
      if (!root) return;
      root.querySelectorAll("[data-friend-slug]").forEach((panel) => {
        const slug = panel.getAttribute("data-friend-slug");
        if (typeof viewerConfig !== "undefined" && viewerConfig.signedIn === false) {
          panel.hidden = true;
          return;
        }
        if (!slug || panel.dataset.friendState === "ready") return;
        const existing = friendLoads.get(slug);
        if (existing) {
          existing
            .then((payload) => renderFriends(slug, payload))
            .catch((err) => markFriendError(slug, err && err.message));
          return;
        }
        const job = fetchFriendRatings(slug);
        friendLoads.set(slug, job);
        job.then((payload) => renderFriends(slug, payload)).catch((err) => {
          friendLoads.delete(slug);
          markFriendError(slug, err && err.message);
        });
      });
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
