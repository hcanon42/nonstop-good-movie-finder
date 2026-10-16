    const tabButtons = [...document.querySelectorAll('[role="tab"][data-tab]')];
    const tabNames = tabButtons.map((button) => button.dataset.tab);
    const pageKey = document.body.dataset.page || "page";
    const VIEW_STORAGE = "gmf-tab-" + pageKey;

    function tabFromHash() {
      const name = decodeURIComponent(location.hash.replace(/^#/, ""));
      return tabNames.includes(name) ? name : null;
    }

    function setView(view, updateHash = true) {
      if (!tabNames.length) return;
      if (!tabNames.includes(view)) view = tabNames[0];
      activeTab = view;
      tabNames.forEach((name) => {
        const panel = document.getElementById(name + "-view");
        if (panel) panel.hidden = name !== view;
      });
      tabButtons.forEach((button) => {
        const selected = button.dataset.tab === view;
        button.setAttribute("aria-selected", selected ? "true" : "false");
        button.tabIndex = selected ? 0 : -1;
      });
      document.querySelectorAll("[data-tools]").forEach((el) => {
        el.hidden = !(el.getAttribute("data-tools") || "").split(/\s+/).includes(view);
      });
      const label = document.getElementById("nav-pills-label");
      if (label) label.textContent = view === "calendar" ? "Show only" : "Jump to";
      if (view !== "calendar") sectionFilter = null;
      if (searchField) {
        const input = document.getElementById("search");
        if (input) {
          input.placeholder = view === "watchlist"
            ? "Filter your watchlist by title…"
            : view === "recommendations"
              ? "Filter by title, director, theme…"
              : "Filter by title, director, genre…";
        }
      }
      try {
        localStorage.setItem(VIEW_STORAGE, view);
      } catch (err) {
        /* private mode or storage full */
      }
      if (updateHash && tabFromHash() !== view) {
        history.pushState(null, "", "#" + view);
      }
      applySearch();
      syncSectionLinks();
      if (view === "plan" && typeof renderPlan === "function") renderPlan();
      updateScrolledSection();
      window.scrollTo(0, Math.min(window.scrollY, 0));
    }

    tabButtons.forEach((button, index) => {
      button.addEventListener("click", () => setView(button.dataset.tab));
      button.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        const next = tabButtons[(index + (event.key === "ArrowRight" ? 1 : tabButtons.length - 1)) % tabButtons.length];
        next.focus();
        setView(next.dataset.tab);
      });
    });
    window.addEventListener("hashchange", () => {
      const view = tabFromHash();
      if (view && view !== activeTab) setView(view, false);
    });
    document.querySelectorAll("[data-wl-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        watchlistFilter = button.getAttribute("data-wl-filter") || "all";
        document.querySelectorAll("[data-wl-filter]").forEach((other) => {
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        });
        applySearch();
      });
    });
    document.querySelectorAll("[data-day]").forEach((button) => {
      button.addEventListener("click", () => toggleDay(button));
    });
    document.querySelectorAll(".day-film-title").forEach((button) => {
      button.addEventListener("click", () => toggleFilmDetail(button));
    });

    const AUDIO_VERSIONS = new Set(["OV", "OmdU", "OmeU"]);
    const NO_SPOKEN_LANGUAGE = "no spoken language";
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
        signedIn: false,
      };
    }

    function viewerEndpoint() {
      if (location.protocol === "file:") {
        return "http://127.0.0.1:" + (viewerConfig.port || 8765) + "/api/viewer";
      }
      return "/api/viewer";
    }

    function followingEndpoint() {
      if (location.protocol === "file:") {
        return "http://127.0.0.1:" + (viewerConfig.port || 8765) + "/api/following";
      }
      return "/api/following";
    }

    let followingPayload = null;
    let followingLoad = null;

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
      if (!language || language === NO_SPOKEN_LANGUAGE) return true;
      if (knownLanguageSet().has(language)) return AUDIO_VERSIONS.has(version);
      return version === "OmeU";
    }

    function filmFollowable(primary, versions) {
      const language = (primary || "").trim().toLowerCase();
      if (!language || language === NO_SPOKEN_LANGUAGE) return true;
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
      document.querySelectorAll(".section-group").forEach((group) => {
        group.hidden = ![...group.querySelectorAll(".program-section")].some((section) => !section.hidden);
      });
      const note = document.getElementById("program-language-note");
      if (note) note.textContent = languageBlurb(selectedLanguageNames());
      const summary = document.getElementById("lang-summary");
      if (summary) {
        const names = selectedLanguageNames();
        summary.textContent = names.length ? names.join(", ") : "English subtitles only";
      }
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
        const mode = row.getAttribute("data-filter");
        const hideUnfollowable = mode === "hide";
        const onlyUnavailable = mode === "unavailable";
        const catalog = mode === "catalog";
        row.setAttribute(
          "data-lang-hide",
          (hideUnfollowable && !follow) || (onlyUnavailable && follow) ? "1" : "0"
        );
        row.classList.toggle(
          "row-no-follow",
          !follow && !hideUnfollowable && !onlyUnavailable && !catalog
        );
        if (!row.hasAttribute("data-search-base")) {
          row.setAttribute("data-search-base", row.getAttribute("data-search") || "");
        }
        const phrase = !follow && !hideUnfollowable && !onlyUnavailable && !catalog
          ? " no followable version"
          : "";
        row.setAttribute("data-search", (row.getAttribute("data-search-base") || "") + phrase);
        const badge = row.querySelector(".follow-badge");
        if (badge) badge.hidden = follow || hideUnfollowable || onlyUnavailable || catalog;
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
      syncSectionLinks();
      updateScrolledSection();
      if (typeof renderPlan === "function") renderPlan();
    }

    function setSettingsOpen(open) {
      const panel = document.getElementById("settings-panel");
      const button = document.getElementById("settings-open");
      if (!panel || !button) return;
      panel.hidden = !open;
      button.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        setAccountSwitchOpen(false);
        const input = panel.querySelector('input[name="letterboxd"]');
        if (input && !viewerConfig.letterboxdUser) input.focus();
      }
    }

    function setAccountSwitchOpen(open) {
      const panel = document.getElementById("account-switch-panel");
      const button = document.getElementById("account-switch-open");
      if (!panel || !button) return;
      panel.hidden = !open;
      button.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        setSettingsOpen(false);
        ensureFollowingList();
      }
    }

    function switchAvatarUrl(value) {
      if (typeof value !== "string" || !value) return null;
      const prefixes = [
        "https://a.ltrbxd.com/",
        "https://s.ltrbxd.com/",
        "https://secure.gravatar.com/",
      ];
      return prefixes.some((prefix) => value.startsWith(prefix)) ? value : null;
    }

    function renderFollowingList(payload) {
      const panel = document.getElementById("account-switch-panel");
      const list = panel && panel.querySelector(".account-switch-list");
      const status = panel && panel.querySelector(".account-switch-status");
      if (!panel || !list || !status) return;
      const members = Array.isArray(payload.members) ? payload.members : [];
      list.replaceChildren();
      if (!members.length) {
        status.textContent = "No Letterboxd friends found.";
        status.hidden = false;
        return;
      }
      status.hidden = true;
      const active = String(payload.activeUser || viewerConfig.letterboxdUser || "").toLowerCase();
      members.forEach((member) => {
        if (!member || typeof member.username !== "string") return;
        const item = document.createElement("button");
        item.type = "button";
        item.className = "account-switch-item";
        item.setAttribute("role", "option");
        const selected = member.username.toLowerCase() === active;
        item.setAttribute("aria-selected", selected ? "true" : "false");
        item.dataset.username = member.username;
        const avatarUrl = switchAvatarUrl(member.avatar);
        if (avatarUrl) {
          const image = document.createElement("img");
          image.className = "account-switch-avatar";
          image.src = avatarUrl;
          image.alt = "";
          item.appendChild(image);
        } else {
          const initial = document.createElement("span");
          initial.className = "account-switch-initial";
          initial.setAttribute("aria-hidden", "true");
          initial.textContent = (member.name || member.username).slice(0, 1);
          item.appendChild(initial);
        }
        const label = document.createElement("span");
        label.className = "account-switch-label";
        const title = document.createElement("span");
        title.textContent = member.name || member.username;
        label.appendChild(title);
        const handle = document.createElement("span");
        handle.className = "account-switch-handle";
        handle.textContent = "@" + member.username;
        label.appendChild(handle);
        item.appendChild(label);
        if (member.isRoot) {
          const tag = document.createElement("span");
          tag.className = "account-switch-handle";
          tag.textContent = "You";
          item.appendChild(tag);
        }
        item.addEventListener("click", () => {
          if (selected) {
            setAccountSwitchOpen(false);
            return;
          }
          switchToProfile(member.username);
        });
        list.appendChild(item);
      });
    }

    function ensureFollowingList() {
      const panel = document.getElementById("account-switch-panel");
      const status = panel && panel.querySelector(".account-switch-status");
      if (!panel || !status) return;
      if (followingPayload) {
        renderFollowingList(followingPayload);
        return;
      }
      status.hidden = false;
      status.textContent = "Loading friends…";
      if (!followingLoad) {
        followingLoad = fetch(followingEndpoint())
          .then(async (response) => {
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || "Could not load friends.");
            return payload;
          })
          .then((payload) => {
            followingPayload = payload;
            renderFollowingList(payload);
          })
          .catch((err) => {
            status.textContent = err.message || "Could not load friends.";
          })
          .finally(() => {
            followingLoad = null;
          });
      }
      return followingLoad;
    }

    function bindAccountSwitch() {
      const openButton = document.getElementById("account-switch-open");
      if (!openButton) return;
      openButton.addEventListener("click", () => {
        const panel = document.getElementById("account-switch-panel");
        setAccountSwitchOpen(!panel || panel.hidden);
      });
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        const panel = document.getElementById("account-switch-panel");
        if (!panel || panel.hidden) return;
        setAccountSwitchOpen(false);
        openButton.focus();
      });
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
      document.querySelectorAll("form[data-profile-form]").forEach((profileForm) => {
        profileForm.addEventListener("submit", loadProfile);
      });
      document.querySelectorAll("[data-open-account]").forEach((link) => {
        link.addEventListener("click", () => setSettingsOpen(true));
      });
      const signOut = document.getElementById("account-signout");
      if (signOut) signOut.addEventListener("click", signOutUser);
      document.addEventListener("click", (event) => {
        const settingsPanel = document.getElementById("settings-panel");
        const switchPanel = document.getElementById("account-switch-panel");
        if (settingsPanel && !settingsPanel.hidden) {
          if (!event.target.closest(".account") && !event.target.closest("[data-open-account]")) {
            setSettingsOpen(false);
          }
        }
        if (switchPanel && !switchPanel.hidden) {
          if (!event.target.closest(".account") && !event.target.closest("[data-open-account]")) {
            setAccountSwitchOpen(false);
          }
        }
      });
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        const panel = document.getElementById("settings-panel");
        if (!panel || panel.hidden) return;
        setSettingsOpen(false);
        if (openButton) openButton.focus();
      });
    }

    function profileSkeletonMarkup() {
      const row = (
        '<div class="skeleton-row">' +
        '<span class="sk sk-rating"></span>' +
        '<span class="sk sk-year"></span>' +
        '<span class="sk sk-title"></span>' +
        '<span class="sk sk-director"></span>' +
        '<span class="sk sk-genres"></span>' +
        "</div>"
      );
      const section = (rows) => (
        '<section class="skeleton-section">' +
        '<div class="skeleton-head">' +
        '<span class="skeleton-rail"></span>' +
        '<span class="skeleton-copy">' +
        '<span class="sk sk-heading"></span>' +
        '<span class="sk sk-subhead"></span>' +
        "</span></div>" +
        `<div class="skeleton-panel">${row.repeat(rows)}</div>` +
        "</section>"
      );
      return section(7) + section(5);
    }

    function setProfileLoading(active) {
      const main = document.querySelector("main");
      if (!main) return;
      let shell = document.getElementById("profile-loading");
      if (!shell) {
        shell = document.createElement("div");
        shell.id = "profile-loading";
        shell.className = "profile-loading";
        shell.hidden = true;
        shell.setAttribute("aria-hidden", "true");
        shell.innerHTML = profileSkeletonMarkup();
        main.prepend(shell);
      }
      shell.hidden = !active;
      document.body.classList.toggle("is-profile-loading", active);
      main.setAttribute("aria-busy", active ? "true" : "false");
      const openButton = document.getElementById("settings-open");
      const nameEl = document.getElementById("account-switch-open")
        || (openButton && openButton.querySelector(".account-name"));
      if (openButton) {
        openButton.disabled = active;
        if (active) openButton.setAttribute("aria-busy", "true");
        else openButton.removeAttribute("aria-busy");
      }
      if (nameEl) {
        if (active) {
          if (!nameEl.dataset.label) nameEl.dataset.label = nameEl.textContent;
          nameEl.textContent = "Loading…";
          nameEl.disabled = true;
          nameEl.setAttribute("aria-busy", "true");
        } else {
          if (nameEl.dataset.label) nameEl.textContent = nameEl.dataset.label;
          nameEl.disabled = false;
          nameEl.removeAttribute("aria-busy");
        }
      }
      if (active) {
        setSettingsOpen(false);
        setAccountSwitchOpen(false);
        window.scrollTo(0, 0);
      }
    }

    async function submitProfile(user, options) {
      const form = options && options.form;
      const input = form && form.querySelector('input[name="letterboxd"]');
      const status = form && form.querySelector(".settings-status");
      const button = form && form.querySelector(".settings-load");
      const trimmed = (user || "").trim().replace(/^@/, "");
      if (!trimmed) {
        if (status) status.textContent = "Enter a Letterboxd username.";
        return;
      }
      const active = trimmed.toLowerCase();
      const letterboxd = String(viewerConfig.letterboxdUser || "").toLowerCase();
      const following = String(
        viewerConfig.followingUser || viewerConfig.letterboxdUser || ""
      ).toLowerCase();
      if (viewerConfig.signedIn && active === letterboxd && active === following) {
        if (status) status.textContent = "This page already uses that profile.";
        setAccountSwitchOpen(false);
        return;
      }
      if (status) status.textContent = "Loading profile…";
      if (button) button.disabled = true;
      followingPayload = null;
      followingLoad = null;
      setProfileLoading(true);
      let navigating = false;
      try {
        const response = await fetch(viewerEndpoint(), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            letterboxdUser: trimmed,
            languages: selectedLanguageNames(),
          }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          if (status) status.textContent = payload.error || "Could not load that profile.";
          if (form) setSettingsOpen(true);
          else setAccountSwitchOpen(true);
          return;
        }
        serverReady = true;
        if (payload.reload) {
          navigating = true;
          window.location.reload();
        } else if (status) status.textContent = "";
      } catch (err) {
        if (status) {
          status.textContent = "Run python3 goodmoviefinder.py --serve, then load the profile again.";
        }
        if (form) setSettingsOpen(true);
        else setAccountSwitchOpen(true);
      } finally {
        if (!navigating) {
          setProfileLoading(false);
          if (button) button.disabled = false;
        }
      }
    }

    function switchToProfile(user) {
      submitProfile(user);
    }

    async function loadProfile(event) {
      event.preventDefault();
      const form = event.currentTarget;
      const input = form.querySelector('input[name="letterboxd"]');
      const user = input && input.value ? input.value : "";
      await submitProfile(user, { form });
    }


    async function signOutUser() {
      setProfileLoading(true);
      let navigating = false;
      try {
        const response = await fetch(viewerEndpoint(), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ signOut: true }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || "Could not sign out.");
        serverReady = true;
        navigating = true;
        window.location.reload();
      } catch (err) {
        const status = document.querySelector("#settings-panel .settings-status");
        if (status) status.textContent = "Run python3 goodmoviefinder.py --serve to sign out.";
        setSettingsOpen(true);
      } finally {
        if (!navigating) setProfileLoading(false);
      }
    }

    function bootViewer() {
      const saved = readSavedLanguages(viewerConfig.stamp);
      if (saved) applyCheckboxSelection(saved);
      applyLanguageFilter();
      bindSettings();
      bindAccountSwitch();
    }

    bootViewer();

    let storedView = tabNames[0];
    try {
      const saved = localStorage.getItem(VIEW_STORAGE);
      if (tabNames.includes(saved)) storedView = saved;
    } catch (err) {
      /* use the first tab */
    }
    setView(tabFromHash() || storedView, false);
    if (!tabFromHash() && tabNames.length) history.replaceState(null, "", "#" + activeTab);

    search.addEventListener("input", applySearch);
    if (searchClear) {
      searchClear.addEventListener("click", () => {
        search.value = "";
        applySearch();
        search.focus();
      });
    }
    document.addEventListener("scroll", updateScrolledSection, { passive: true });
