    const PLAN_LIMIT = 8;
    const PLAN_STORAGE = "gmf-plan";
    const ASSUMED_RUNTIME = 150;
    const SAME_VENUE_GAP = 20;
    const OTHER_VENUE_GAP = 50;
    // Larger than a morning versus a weekend evening, so two films share a day
    // only when one of them has no day of its own.
    const DOUBLE_BILL_PENALTY = 140;
    const UNREACHABLE = -1e15;
    const PLAN_WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const PLAN_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const PLAN_MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    const PLAN_OPTION_MODES = [
      ["best", "Best"],
      ["soon", "Soonest"],
      ["weekend", "Weekends"],
    ];

    function readCatalog() {
      const node = document.getElementById("plan-catalog");
      const map = new Map();
      if (!node) return map;
      try {
        const parsed = JSON.parse(node.textContent || "[]");
        if (!Array.isArray(parsed)) return map;
        parsed.forEach((film) => {
          if (film && typeof film.id === "string") map.set(film.id, film);
        });
      } catch (err) {
        /* catalog is static page data */
      }
      return map;
    }

    function parseMinutes(time) {
      const match = /^(\d{1,2}):(\d{2})$/.exec(time || "");
      if (!match) return null;
      const hours = Number(match[1]);
      const minutes = Number(match[2]);
      if (hours > 23 || minutes > 59) return null;
      return hours * 60 + minutes;
    }

    function dayIndex(year, month, day) {
      return Math.floor(Date.UTC(year, month - 1, day) / 86400000);
    }

    function minutesOnDate(iso, minutes) {
      const parts = String(iso || "").split("-").map(Number);
      if (parts.length !== 3 || parts.some((part) => Number.isNaN(part))) return null;
      return dayIndex(parts[0], parts[1], parts[2]) * 1440 + minutes;
    }

    function absoluteStamp(value) {
      const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || "");
      if (!match) return null;
      const hour = Number(match[4]);
      const minute = Number(match[5]);
      if (hour > 23 || minute > 59) return null;
      return dayIndex(Number(match[1]), Number(match[2]), Number(match[3])) * 1440 + hour * 60 + minute;
    }

    function readBusy() {
      const node = document.getElementById("plan-busy");
      const ranges = [];
      if (!node) return ranges;
      try {
        const parsed = JSON.parse(node.textContent || "{}");
        const items = parsed && Array.isArray(parsed.intervals) ? parsed.intervals : [];
        items.forEach((item) => {
          const start = absoluteStamp(item && item.start);
          const end = absoluteStamp(item && item.end);
          if (start == null || end == null || end <= start) return;
          ranges.push({ start, end });
        });
      } catch (err) {
        /* busy times are static page data */
      }
      return ranges;
    }

    function overlapsBusy(start, end, busy) {
      return busy.some((block) => start < block.end && end > block.start);
    }

    function weekdayIndex(iso) {
      const parts = iso.split("-").map(Number);
      return new Date(parts[0], parts[1] - 1, parts[2]).getDay();
    }

    function daysFromToday(iso, todayIso) {
      const film = iso.split("-").map(Number);
      const today = todayIso.split("-").map(Number);
      const filmUtc = Date.UTC(film[0], film[1] - 1, film[2]);
      const todayUtc = Date.UTC(today[0], today[1] - 1, today[2]);
      return Math.round((filmUtc - todayUtc) / 86400000);
    }

    function isUpcoming(iso, start, todayIso, minutesNow) {
      if (iso > todayIso) return true;
      if (iso < todayIso) return false;
      return start > minutesNow;
    }

    function timeQuality(start, weekend) {
      if (start >= 18 * 60 && start <= 21 * 60 + 30) return 50;
      if (start >= 17 * 60 && start < 18 * 60) return 28;
      if (start > 21 * 60 + 30 && start <= 22 * 60 + 30) return 28;
      if (start > 22 * 60 + 30) return 5;
      if (start >= 14 * 60 && start < 17 * 60) return weekend ? 8 : -8;
      return -25;
    }

    function slotScore(iso, start, todayIso, mode) {
      const weekday = weekdayIndex(iso);
      const weekend = weekday === 0 || weekday === 6;
      const days = daysFromToday(iso, todayIso);
      let score = timeQuality(start, weekend);
      if (mode === "soon") {
        if (weekend) score += 24;
        else if (weekday === 5) score += 8;
        return score - days * 18;
      }
      if (mode === "weekend") {
        if (weekend) score += 100;
        else if (weekday === 5) score += 8;
        return score - days;
      }
      if (weekend) score += 40;
      else if (weekday === 5) score += 12;
      return score - days;
    }

    function runtimeMinutes(film) {
      return typeof film.duration === "number" && film.duration > 0
        ? film.duration
        : ASSUMED_RUNTIME;
    }

    function screeningFollowable(film, screening) {
      if (!window.gmfViewer) return true;
      return window.gmfViewer.versionFollowable(
        film.primaryLanguage || "",
        screening.language || ""
      );
    }

    function slotsFor(film, todayIso, minutesNow, mode, busy) {
      const runtime = runtimeMinutes(film);
      const assumed = !(typeof film.duration === "number" && film.duration > 0);
      const slots = [];
      let upcoming = 0;
      let blocked = 0;
      const screenings = Array.isArray(film.screenings) ? film.screenings : [];
      screenings.forEach((screening) => {
        if (!screeningFollowable(film, screening)) return;
        const start = parseMinutes(screening.time);
        if (start == null || typeof screening.date !== "string") return;
        if (!isUpcoming(screening.date, start, todayIso, minutesNow)) return;
        upcoming += 1;
        const startAbs = minutesOnDate(screening.date, start);
        if (startAbs != null && overlapsBusy(startAbs, startAbs + runtime, busy)) {
          blocked += 1;
          return;
        }
        slots.push({
          filmId: film.id,
          title: film.title || "Film",
          date: screening.date,
          time: screening.time,
          start,
          end: start + runtime,
          venue: screening.venue || "Cinema",
          venueSlug: screening.venueSlug || "",
          language: screening.language || "",
          url: typeof screening.url === "string" ? screening.url : "",
          assumed,
          score: slotScore(screening.date, start, todayIso, mode),
        });
      });
      return { slots, upcoming, blocked };
    }

    function fitsTogether(first, second) {
      const earlier = first.start <= second.start ? first : second;
      const later = earlier === first ? second : first;
      const sameVenue = earlier.venueSlug && earlier.venueSlug === later.venueSlug;
      const gap = sameVenue ? SAME_VENUE_GAP : OTHER_VENUE_GAP;
      return later.start >= earlier.end + gap;
    }

    function bestSlot(slots) {
      return slots.reduce((best, slot) => (slot.score > best.score ? slot : best));
    }

    function bestPair(leftSlots, rightSlots) {
      let best = null;
      leftSlots.forEach((left) => {
        rightSlots.forEach((right) => {
          if (!fitsTogether(left, right)) return;
          const score = left.score + right.score;
          if (!best || score > best.score) best = { score, slots: [left, right] };
        });
      });
      return best;
    }

    function bitCount(mask) {
      let count = 0;
      let rest = mask;
      while (rest) {
        count += rest & 1;
        rest >>>= 1;
      }
      return count;
    }

    function isEvening(slot) {
      return slot.start >= 18 * 60 && slot.start <= 21 * 60 + 30;
    }

    function isWeekend(slot) {
      const weekday = weekdayIndex(slot.date);
      return weekday === 0 || weekday === 6;
    }

    function summaryText(rows) {
      if (!rows.length) return "None of these films can be placed on the current program.";
      const films = rows.length === 1 ? "1 film" : `${rows.length} films`;
      const dayCount = new Set(rows.map((row) => row.date)).size;
      const days = dayCount === 1 ? "1 day" : `${dayCount} days`;
      const clauses = [`${films} across ${days}`];
      const perDay = new Map();
      rows.forEach((row) => perDay.set(row.date, (perDay.get(row.date) || 0) + 1));
      const doubles = [...perDay.values()].filter((count) => count > 1).length;
      if (doubles === 1) clauses.push("one double bill");
      else if (doubles > 1) clauses.push(`${doubles} double bills`);
      if (rows.every(isEvening)) clauses.push("all evenings");
      if (rows.every(isWeekend)) clauses.push("all on weekends");
      return `${clauses.join(", ")}.`;
    }

    function leftOutReason(film, packed) {
      const screenings = Array.isArray(film.screenings) ? film.screenings : [];
      const followable = screenings.some((screening) => screeningFollowable(film, screening));
      if (packed.blocked > 0 && packed.upcoming > 0 && packed.blocked === packed.upcoming) {
        return "Every upcoming screening overlaps your calendar";
      }
      return followable ? "No upcoming screening" : "No followable screening";
    }

    function planSchedule(films, todayIso, minutesNow, mode) {
      const unscheduled = [];
      const active = [];
      films.forEach((film) => {
        const packed = slotsFor(film, todayIso, minutesNow, mode || "best", planBusy);
        if (packed.slots.length) {
          active.push({ film, slots: packed.slots });
          return;
        }
        unscheduled.push({
          id: film.id,
          title: film.title || "Film",
          reason: leftOutReason(film, packed),
        });
      });
      if (!active.length) {
        return { rows: [], unscheduled, summary: summaryText([]) };
      }

      const days = [];
      const seenDays = new Set();
      active.forEach((entry) => {
        entry.slots.forEach((slot) => {
          if (seenDays.has(slot.date)) return;
          seenDays.add(slot.date);
          days.push(slot.date);
        });
      });
      days.sort();

      const byDay = active.map(() => new Map());
      active.forEach((entry, index) => {
        entry.slots.forEach((slot) => {
          const list = byDay[index].get(slot.date) || [];
          list.push(slot);
          byDay[index].set(slot.date, list);
        });
      });

      const filmCount = active.length;
      const full = 1 << filmCount;
      const scores = Array.from({ length: days.length + 1 }, () => Array(full).fill(UNREACHABLE));
      const choice = Array.from({ length: days.length + 1 }, () => Array(full).fill(null));
      scores[0][0] = 0;

      function consider(nextDay, nextMask, score, prevMask, slots) {
        if (score <= scores[nextDay][nextMask]) return;
        scores[nextDay][nextMask] = score;
        choice[nextDay][nextMask] = { prev: prevMask, slots };
      }

      for (let dayIndex = 0; dayIndex < days.length; dayIndex++) {
        const day = days[dayIndex];
        for (let mask = 0; mask < full; mask++) {
          const base = scores[dayIndex][mask];
          if (base <= UNREACHABLE / 2) continue;
          consider(dayIndex + 1, mask, base, mask, []);
          for (let bit = 0; bit < filmCount; bit++) {
            if (mask & (1 << bit)) continue;
            const slots = byDay[bit].get(day);
            if (!slots) continue;
            const slot = bestSlot(slots);
            consider(dayIndex + 1, mask | (1 << bit), base + slot.score, mask, [slot]);
          }
          for (let left = 0; left < filmCount; left++) {
            if (mask & (1 << left)) continue;
            const leftSlots = byDay[left].get(day);
            if (!leftSlots) continue;
            for (let right = left + 1; right < filmCount; right++) {
              if (mask & (1 << right)) continue;
              const rightSlots = byDay[right].get(day);
              if (!rightSlots) continue;
              const pair = bestPair(leftSlots, rightSlots);
              if (!pair) continue;
              const next = mask | (1 << left) | (1 << right);
              consider(
                dayIndex + 1,
                next,
                base + pair.score - DOUBLE_BILL_PENALTY,
                mask,
                pair.slots,
              );
            }
          }
        }
      }

      let bestMask = 0;
      let bestCount = -1;
      let bestScore = UNREACHABLE;
      for (let mask = 0; mask < full; mask++) {
        const score = scores[days.length][mask];
        if (score <= UNREACHABLE / 2) continue;
        const count = bitCount(mask);
        if (count > bestCount || (count === bestCount && score > bestScore)) {
          bestMask = mask;
          bestCount = count;
          bestScore = score;
        }
      }

      const rows = [];
      let mask = bestMask;
      for (let dayIndex = days.length; dayIndex > 0; dayIndex--) {
        const step = choice[dayIndex][mask];
        if (!step) break;
        rows.push(...step.slots);
        mask = step.prev;
      }
      rows.sort((a, b) => a.date.localeCompare(b.date) || a.start - b.start || a.title.localeCompare(b.title));

      const placed = new Set(rows.map((row) => row.filmId));
      active.forEach((entry) => {
        if (placed.has(entry.film.id)) return;
        unscheduled.push({
          id: entry.film.id,
          title: entry.film.title || "Film",
          reason: "No screening fits alongside the others",
        });
      });
      return { rows, unscheduled, summary: summaryText(rows) };
    }

    function planChoices(films, todayIso, minutesNow) {
      const seen = new Set();
      const choices = [];
      PLAN_OPTION_MODES.forEach(([mode, label]) => {
        const result = planSchedule(films, todayIso, minutesNow, mode);
        const key = result.rows
          .map((row) => `${row.filmId}|${row.date}|${row.time}`)
          .join(";");
        if (choices.length && seen.has(key)) return;
        seen.add(key);
        choices.push({ mode, label, result });
      });
      return choices;
    }

    function escapeHtml(value) {
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function cellFilmHtml(row) {
      const language = row.language ? ` · ${escapeHtml(row.language)}` : "";
      const assumed = row.assumed
        ? '<span class="plan-assumed">Runtime unknown, using 2.5 hours.</span>'
        : "";
      return (
        `<div class="plan-cell-film">` +
        `<span class="plan-time">${escapeHtml(row.time)}</span>` +
        `<span class="plan-cell-title">${escapeHtml(row.title)}</span>` +
        `<span class="plan-meta">${venueHtml(row)}${language}</span>` +
        `${assumed}</div>`
      );
    }

    function planMonthHtml(year, month, byDate, todayIso) {
      const first = new Date(year, month - 1, 1);
      const lead = (first.getDay() + 6) % 7;
      const daysInMonth = new Date(year, month, 0).getDate();
      const cells = [];
      for (let index = 0; index < lead; index++) cells.push('<span class="plan-cell pad"></span>');
      for (let day = 1; day <= daysInMonth; day++) {
        const iso = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
        const films = byDate.get(iso) || [];
        const todayClass = iso === todayIso ? " is-today" : "";
        if (!films.length) {
          cells.push(
            `<span class="plan-cell blank${todayClass}"><span class="plan-cell-num">${day}</span></span>`,
          );
          continue;
        }
        cells.push(
          `<div class="plan-cell has${todayClass}">` +
          `<span class="plan-cell-num">${day}</span>` +
          `${films.map(cellFilmHtml).join("")}</div>`,
        );
      }
      while (cells.length % 7) cells.push('<span class="plan-cell pad"></span>');
      const weeks = [];
      for (let index = 0; index < cells.length; index += 7) {
        weeks.push(`<div class="plan-week">${cells.slice(index, index + 7).join("")}</div>`);
      }
      const heads = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
        .map((name) => `<span>${name}</span>`)
        .join("");
      return (
        `<section class="plan-month">` +
        `<h2>${PLAN_MONTHS_LONG[month - 1]} ${year}</h2>` +
        `<div class="plan-month-head">${heads}</div>` +
        `${weeks.join("")}</section>`
      );
    }

    function planCalendarHtml(rows, todayIso) {
      if (!rows.length) return "";
      const byDate = new Map();
      rows.forEach((row) => {
        const list = byDate.get(row.date) || [];
        list.push(row);
        byDate.set(row.date, list);
      });
      const dates = [...byDate.keys()].sort();
      let year = Number(dates[0].slice(0, 4));
      let month = Number(dates[0].slice(5, 7));
      const endYear = Number(dates[dates.length - 1].slice(0, 4));
      const endMonth = Number(dates[dates.length - 1].slice(5, 7));
      const months = [];
      while (year < endYear || (year === endYear && month <= endMonth)) {
        months.push(planMonthHtml(year, month, byDate, todayIso));
        month += 1;
        if (month === 13) {
          month = 1;
          year += 1;
        }
      }
      return `<div class="plan-months">${months.join("")}</div>`;
    }

    function venueHtml(slot) {
      const venue = escapeHtml(slot.venue || "Cinema");
      if (slot.url.startsWith("https://") || slot.url.startsWith("http://")) {
        return `<a class="showtime-link" href="${escapeHtml(slot.url)}" target="_blank" rel="noopener noreferrer">${venue}</a>`;
      }
      return venue;
    }

    function chipHtml(film) {
      return (
        `<button type="button" class="plan-chip" data-plan-id="${escapeHtml(film.id)}">` +
        `${escapeHtml(film.title)} <span aria-hidden="true">×</span>` +
        `<span class="visually-hidden">Remove from plan</span></button>`
      );
    }

    const planCatalog = readCatalog();
    const planBusy = readBusy();
    let planSelected = [];
    try {
      const raw = JSON.parse(localStorage.getItem(PLAN_STORAGE) || "[]");
      if (Array.isArray(raw)) {
        raw.forEach((id) => {
          if (typeof id === "string" && planCatalog.has(id) && !planSelected.includes(id)) {
            planSelected.push(id);
          }
        });
        planSelected = planSelected.slice(0, PLAN_LIMIT);
      }
    } catch (err) {
      planSelected = [];
    }

    function writePlan() {
      try {
        localStorage.setItem(PLAN_STORAGE, JSON.stringify(planSelected));
      } catch (err) {
        /* private mode or storage full */
      }
    }

    function syncPlanControls() {
      const chosen = new Set(planSelected);
      document.querySelectorAll(".plan-add").forEach((button) => {
        const on = chosen.has(button.getAttribute("data-plan-id"));
        button.setAttribute("aria-pressed", on ? "true" : "false");
        button.title = on ? "Remove from plan" : "Add to plan";
        const icon = button.querySelector(".plan-add-icon");
        const label = button.querySelector(".visually-hidden");
        if (icon) icon.textContent = on ? "✓" : "+";
        if (label) label.textContent = on ? "Remove from plan" : "Add to plan";
      });
      document.querySelectorAll("[data-movie-row][data-plan-id], .day-film[data-plan-id]").forEach((el) => {
        el.classList.toggle("is-planned", chosen.has(el.getAttribute("data-plan-id")));
      });
      const count = document.getElementById("plan-count");
      if (count) {
        count.hidden = planSelected.length === 0;
        count.textContent = planSelected.length ? String(planSelected.length) : "";
      }
    }

    function markPlannedDays(rows) {
      const dates = new Set(rows.map((row) => row.date));
      document.querySelectorAll("[data-day]").forEach((button) => {
        button.classList.toggle("is-planned-day", dates.has(button.getAttribute("data-day")));
      });
    }

    let planMode = "best";

    function renderPlan() {
      const empty = document.getElementById("plan-empty");
      const picked = document.getElementById("plan-picked");
      const chips = document.getElementById("plan-chips");
      const options = document.getElementById("plan-options");
      const summary = document.getElementById("plan-summary");
      const calendar = document.getElementById("plan-schedule");
      const left = document.getElementById("plan-left-out");
      const leftList = document.getElementById("plan-left-out-list");
      if (!empty || !picked || !chips || !options || !summary || !calendar || !left || !leftList) return;
      if (!planSelected.length) {
        empty.hidden = false;
        picked.hidden = true;
        chips.replaceChildren();
        options.hidden = true;
        options.replaceChildren();
        summary.hidden = true;
        calendar.hidden = true;
        calendar.replaceChildren();
        left.hidden = true;
        markPlannedDays([]);
        return;
      }
      const films = planSelected.map((id) => planCatalog.get(id)).filter(Boolean);
      const now = new Date();
      const todayIso = [
        now.getFullYear(),
        String(now.getMonth() + 1).padStart(2, "0"),
        String(now.getDate()).padStart(2, "0"),
      ].join("-");
      const choices = planChoices(films, todayIso, now.getHours() * 60 + now.getMinutes());
      if (!choices.some((choice) => choice.mode === planMode)) planMode = choices[0].mode;
      const active = choices.find((choice) => choice.mode === planMode) || choices[0];
      const result = active.result;
      empty.hidden = true;
      picked.hidden = false;
      chips.innerHTML = films.map(chipHtml).join("");
      options.hidden = choices.length < 2;
      options.innerHTML = choices.map((choice) => (
        `<button type="button" class="plan-option" data-plan-option="${choice.mode}" ` +
        `aria-pressed="${choice.mode === active.mode ? "true" : "false"}">${choice.label}</button>`
      )).join("");
      summary.hidden = false;
      summary.textContent = result.summary;
      if (result.rows.length) {
        calendar.hidden = false;
        calendar.innerHTML = planCalendarHtml(result.rows, todayIso);
      } else {
        calendar.hidden = true;
        calendar.replaceChildren();
      }
      if (result.unscheduled.length) {
        left.hidden = false;
        leftList.innerHTML = result.unscheduled
          .map((item) => `<li><strong>${escapeHtml(item.title)}</strong> — ${escapeHtml(item.reason)}</li>`)
          .join("");
      } else {
        left.hidden = true;
        leftList.replaceChildren();
      }
      markPlannedDays(result.rows);
    }

    function togglePlan(id) {
      if (!planCatalog.has(id)) return;
      const index = planSelected.indexOf(id);
      const cap = document.getElementById("plan-cap");
      if (index >= 0) {
        planSelected.splice(index, 1);
        if (cap) cap.hidden = true;
      } else if (planSelected.length >= PLAN_LIMIT) {
        if (cap) cap.hidden = false;
        return;
      } else {
        planSelected.push(id);
        if (cap) cap.hidden = true;
      }
      writePlan();
      syncPlanControls();
      renderPlan();
    }

    function clearPlan() {
      if (!planSelected.length) return;
      planSelected = [];
      const cap = document.getElementById("plan-cap");
      if (cap) cap.hidden = true;
      writePlan();
      syncPlanControls();
      renderPlan();
    }

    document.querySelectorAll(".plan-add").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        togglePlan(button.getAttribute("data-plan-id"));
      });
    });
    const planChips = document.getElementById("plan-chips");
    if (planChips) {
      planChips.addEventListener("click", (event) => {
        const button = event.target.closest(".plan-chip");
        if (!button) return;
        togglePlan(button.getAttribute("data-plan-id"));
      });
    }
    const planClear = document.getElementById("plan-clear");
    if (planClear) planClear.addEventListener("click", clearPlan);
    const planOptions = document.getElementById("plan-options");
    if (planOptions) {
      planOptions.addEventListener("click", (event) => {
        const button = event.target.closest(".plan-option");
        if (!button) return;
        planMode = button.getAttribute("data-plan-option") || "best";
        renderPlan();
      });
    }
    syncPlanControls();
    renderPlan();
