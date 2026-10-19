(function () {
  var stepsRoot = document.getElementById("build-steps");
  var overallBar = document.getElementById("build-overall");
  var overallFill = overallBar && overallBar.querySelector("span");
  var overallLabel = document.getElementById("build-overall-label");
  var tagline = document.getElementById("build-tagline");
  var errorBox = document.getElementById("build-error");
  var timer = null;

  function pct(value) {
    return Math.max(0, Math.min(100, Math.round(value * 100)));
  }

  function fillBar(bar, step) {
    var span = bar.querySelector("span");
    var total = typeof step.total === "number" && step.total > 0 ? step.total : null;
    var known = total !== null && typeof step.current === "number";
    if (step.status === "running" && !known) {
      bar.classList.add("is-indeterminate");
      span.style.width = "";
      return;
    }
    bar.classList.remove("is-indeterminate");
    var width = 0;
    if (step.status === "done" || step.status === "skipped") width = 100;
    else if (known) width = pct(step.current / total);
    span.style.width = width + "%";
  }

  function onProgramPath() {
    var path = location.pathname.replace(/\/+$/, "") || "/";
    return path === "/" || path === "/index.html" || path === "/program-ranked.html";
  }

  function render(data) {
    if (data.phase === "ready" || (data.programReady && onProgramPath())) {
      window.location.reload();
      return;
    }
    var percent = pct(data.overall || 0);
    if (overallFill) overallFill.style.width = percent + "%";
    if (overallLabel) overallLabel.textContent = percent + "%";
    var running = (data.steps || []).find(function (step) {
      return step.status === "running";
    });
    if (tagline) {
      if (data.phase === "error") {
        tagline.textContent = "The build stopped.";
      } else if (running) {
        tagline.textContent = running.detail
          ? running.label + " — " + running.detail
          : running.label + "…";
      } else {
        tagline.textContent = "Fetching the Nonstop program and Letterboxd data.";
      }
    }
    if (errorBox) {
      if (data.error) {
        errorBox.hidden = false;
        errorBox.textContent = data.error;
      } else {
        errorBox.hidden = true;
        errorBox.textContent = "";
      }
    }
    (data.steps || []).forEach(function (step) {
      var row = stepsRoot.querySelector('[data-step="' + step.id + '"]');
      if (!row) return;
      row.className = "build-step is-" + step.status;
      var count = row.querySelector(".build-step-count");
      if (count) {
        if (typeof step.total === "number" && step.total > 0) {
          count.textContent = (step.current || 0) + " / " + step.total;
        } else if (step.status === "running" && step.current) {
          count.textContent = String(step.current);
        } else {
          count.textContent = "";
        }
      }
      var detail = row.querySelector(".build-step-detail");
      if (detail) {
        if (step.status === "pending") detail.textContent = "Waiting";
        else if (step.status === "skipped") detail.textContent = step.detail || "Skipped";
        else detail.textContent = step.detail || "";
      }
      fillBar(row.querySelector(".build-bar"), step);
    });
    if (data.phase === "error" && timer) {
      window.clearInterval(timer);
      timer = null;
    }
  }

  function poll() {
    fetch("/api/progress", { cache: "no-store" })
      .then(function (response) {
        return response.json();
      })
      .then(render)
      .catch(function () {});
  }

  poll();
  timer = window.setInterval(poll, 400);
})();
