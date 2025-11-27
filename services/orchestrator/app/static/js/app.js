const navLinks = Array.from(document.querySelectorAll("nav a[data-page]"));
    const pages = Array.from(document.querySelectorAll(".page"));
    const jobRows = document.querySelector("#job-rows");
    const scanResult = document.querySelector("#scan-result");
    const queueStatus = document.querySelector("#queue-status");
    const queueMetrics = document.querySelector("#queue-metrics");
    const gpuMetrics = document.querySelector("#gpu-metrics");
    const pauseToggle = document.querySelector("#pause-toggle");
    const logList = document.querySelector("#log-list");
    const logSource = document.querySelector("#log-source");
    const logCategory = document.querySelector("#log-category");
    const logLevel = document.querySelector("#log-level");
    const logQuery = document.querySelector("#log-query");
    const configResult = document.querySelector("#config-result");
    const logRetention = document.querySelector("#log-retention");
    const logConfigResult = document.querySelector("#log-config-result");
    const logStats = document.querySelector("#log-stats");
    const profileNameInput = document.querySelector("#profile-name");
    const libraryProfiles = document.querySelector("#library-profiles");
    const entryRows = document.querySelector("#entry-rows");
    const entrySummary = document.querySelector("#entry-summary");
    const entrySearch = document.querySelector("#entry-search");
    const entryLibraryFilter = document.querySelector("#entry-library-filter");
    const entryStatusFilter = document.querySelector("#entry-status-filter");
    const entryPageSize = document.querySelector("#entry-page-size");
    const entryPageIndicator = document.querySelector("#entry-page-indicator");
    const refreshEntriesBtn = document.querySelector("#refresh-entries");
    const entryLoadMore = document.querySelector("#entry-load-more");
    const entryLoading = document.querySelector("#entry-loading");
    const libraryStatus = document.querySelector("#library-status");
    const libraryCreateForm = document.querySelector("#library-create-form");
    const libraryNameInput = document.querySelector("#library-name");
    const libraryPathInput = document.querySelector("#library-path");
    const libraryProfileSelect = document.querySelector("#library-profile");
    const libraryCreateResult = document.querySelector("#library-create-result");
    const clearProcessedButton = document.querySelector("#clear-processed");
    const ffmpegPreview = document.querySelector("#ffmpeg-preview");
    const lookaheadInput = document.querySelector("#profile-lookahead");
    const lookaheadValue = document.querySelector("#lookahead-value");
    const bframesSelect = document.querySelector("#profile-bframes");
    const adaptiveBframesSelect = document.querySelector("#profile-adaptive-bframes");
    const aqSelect = document.querySelector("#profile-aq");
    const spatialAqSelect = document.querySelector("#profile-spatial-aq");
    const temporalAqSelect = document.querySelector("#profile-temporal-aq");
    const aqStrengthInput = document.querySelector("#profile-aq-strength");
    const aqStrengthValue = document.querySelector("#aq-strength-value");
    const aqStrengthWrapper = document.querySelector("#aq-strength-wrapper");
    const profileTierSelect = document.querySelector("#profile-tier");
    const profileLevelSelect = document.querySelector("#profile-level");
    const profileResolutionSelect = document.querySelector("#profile-resolution");
    const profileFpsSelect = document.querySelector("#profile-maxfps");
    const profilePresetSelect = document.querySelector("#profile-preset");
    const profileRcSelect = document.querySelector("#profile-rc");
    const profileCqSelect = document.querySelector("#profile-cq");
    const profileBitrateSelect = document.querySelector("#profile-bitrate");
    const profileMaxrateSelect = document.querySelector("#profile-maxrate");
    const profileBufsizeSelect = document.querySelector("#profile-bufsize");
    const audioBitrateSelect = document.querySelector("#profile-audio-bitrate");

    let configCache = null;
    let libraryEntries = [];
    let entryOffset = 0;
    let entryTotal = null;
    let entryLoadingState = false;
    let entryHasMore = true;
    let jobCache = [];
    let liveSocket = null;
    let isWsl2 = false;

    if (!logLevel.value) {
      logLevel.value = "INFO";
    }

    function ensureOption(select, value, label) {
      if (value === undefined || value === null) return;
      const stringValue = String(value);
      const exists = Array.from(select.options).some((option) => option.value === stringValue);
      if (!exists) {
        const option = document.createElement("option");
        option.value = stringValue;
        option.textContent = label || stringValue;
        select.appendChild(option);
      }
      select.value = stringValue;
    }

    function setLabelDisabled(label, disabled) {
      if (!label) return;
      const input = label.querySelector("input, select");
      if (input) input.disabled = disabled;
      label.classList.toggle("disabled", disabled);
    }

    function minimumLevelFor(resolution, fpsValue) {
      const [width, height] = String(resolution || "0x0")
        .split("x")
        .map((part) => Number(part) || 0);
      const fps = Number(fpsValue || 30);

      if (width <= 1280 && height <= 720) {
        return fps <= 30 ? 3.1 : 4.0;
      }
      if (width <= 1920 && height <= 1080) {
        return fps <= 30 ? 4.1 : 4.2;
      }
      return 4.2;
    }

    function resolutionPixels(resolution) {
      const [w, h] = String(resolution || "0x0")
        .split("x")
        .map((part) => Number(part) || 0);
      return w * h;
    }

    function normalizeDisplayPath(value) {
      if (!value) return "";
      return String(value).replace(/\\+/g, "/").replace(/^\/watch\//i, "/media/");
    }

    function fileNameFromPath(pathValue) {
      const normalized = normalizeDisplayPath(pathValue);
      const segments = normalized.split("/").filter(Boolean);
      return segments[segments.length - 1] || normalized;
    }

    function jobElapsedSeconds(job) {
      if (Number.isFinite(job?.elapsed_seconds)) {
        return job.elapsed_seconds;
      }
      const created = job?.created_at ? new Date(job.created_at) : null;
      if (!created || Number.isNaN(created.getTime())) {
        return 0;
      }
      const status = String(job?.status || "").toLowerCase();
      const updated = job?.updated_at ? new Date(job.updated_at) : null;
      const endDate =
        status === "completed" || status === "failed"
          ? updated && !Number.isNaN(updated.getTime())
            ? updated
            : created
          : new Date();
      const elapsedMs = endDate.getTime() - created.getTime();
      return Math.max(0, Math.round(elapsedMs / 1000));
    }

    function formatElapsed(seconds) {
      if (!Number.isFinite(seconds) || seconds <= 0) {
        return "<1s";
      }
      const mins = Math.floor(seconds / 60);
      const hrs = Math.floor(mins / 60);
      const days = Math.floor(hrs / 24);
      if (days > 0) {
        return `${days}d ${hrs % 24}h`;
      }
      if (hrs > 0) {
        return `${hrs}h ${mins % 60}m`;
      }
      return `${mins}m ${Math.max(1, Math.round(seconds % 60))}s`;
    }

    function pickClosestFps(allowedValues, preferred) {
      if (!allowedValues.length) return null;
      const sorted = [...allowedValues].sort((a, b) => a - b);
      const notAbovePreferred = sorted.filter((value) => value <= preferred);
      return (notAbovePreferred.pop() ?? sorted[sorted.length - 1]) || sorted[0];
    }

    function enforceLevelConstraints(trigger = "load") {
      const preferredResolution = profileResolutionSelect.value;
      const preferredFps = Number(profileFpsSelect.value || "30");
      const selectedLevel = parseFloat(profileLevelSelect.value || "0") || 0;
      const requiredLevel = minimumLevelFor(preferredResolution, preferredFps);

      let targetLevel = selectedLevel || requiredLevel;
      if (trigger !== "level" && targetLevel < requiredLevel) {
        targetLevel = requiredLevel;
        const levelOption = Array.from(profileLevelSelect.options).find(
          (option) => parseFloat(option.value) >= requiredLevel,
        );
        if (levelOption) {
          profileLevelSelect.value = levelOption.value;
        }
      }

      const fpsOptions = Array.from(profileFpsSelect.options).map((option) => ({
        value: Number(option.value),
        option,
      }));
      const resolutionOptions = Array.from(profileResolutionSelect.options);

      const allowedResolutions = resolutionOptions.filter((option) =>
        fpsOptions.some(({ value: fps }) => minimumLevelFor(option.value, fps) <= targetLevel),
      );

      resolutionOptions.forEach((option) => {
        option.disabled = !allowedResolutions.includes(option);
      });

      let resolution = preferredResolution;
      if (!allowedResolutions.some((opt) => opt.value === resolution)) {
        resolution =
          allowedResolutions
            .sort((a, b) => resolutionPixels(b.value) - resolutionPixels(a.value))
            .find(Boolean)?.value || resolution;
        if (resolution) {
          profileResolutionSelect.value = resolution;
        }
      }

      const allowedFpsValues = fpsOptions
        .filter(({ value }) => minimumLevelFor(resolution, value) <= targetLevel)
        .map(({ value }) => value);

      fpsOptions.forEach(({ option, value }) => {
        option.disabled = !allowedFpsValues.includes(value);
      });

      let fps = preferredFps;
      if (!allowedFpsValues.includes(fps)) {
        const fallbackFps = pickClosestFps(allowedFpsValues, preferredFps);
        if (fallbackFps !== null && fallbackFps !== undefined) {
          fps = fallbackFps;
          profileFpsSelect.value = String(fps);
        }
      }

      const minimumLevel = minimumLevelFor(resolution, fps);
      profileLevelSelect.querySelectorAll("option").forEach((option) => {
        option.disabled = parseFloat(option.value) < minimumLevel;
      });
      if (parseFloat(profileLevelSelect.value) < minimumLevel) {
        const bumpedLevel =
          Array.from(profileLevelSelect.options).find(
            (option) => parseFloat(option.value) >= minimumLevel,
          )?.value || profileLevelSelect.value;
        profileLevelSelect.value = bumpedLevel;
        targetLevel = parseFloat(bumpedLevel) || targetLevel;
      }

      // If the user explicitly lowered the level, keep resolution/FPS within that cap.
      if (trigger === "level") {
        const stillTooHigh =
          minimumLevelFor(profileResolutionSelect.value, Number(profileFpsSelect.value || "0")) >
          targetLevel;
        if (stillTooHigh) {
          const fallback = pickClosestFps(
            allowedFpsValues,
            Number(profileFpsSelect.value || preferredFps),
          );
          if (fallback !== null && fallback !== undefined) {
            profileFpsSelect.value = String(fallback);
          }
        }
      }
    }

    function syncLookaheadDisplay() {
      if (lookaheadValue) {
        lookaheadValue.textContent = lookaheadInput?.value || "0";
      }
    }

    function updateAqState() {
      const enabled = aqSelect.value === "1";
      if (!enabled) {
        spatialAqSelect.value = "0";
        temporalAqSelect.value = "0";
      }
      setLabelDisabled(spatialAqSelect.closest("label"), !enabled);
      setLabelDisabled(temporalAqSelect.closest("label"), !enabled);
      setLabelDisabled(aqStrengthWrapper, !enabled);
    }

    function updateBframeState() {
      const requiresZero = profileTierSelect.value === "baseline";
      if (requiresZero) {
        bframesSelect.value = "0";
      }
      setLabelDisabled(bframesSelect.closest("label"), requiresZero);

      const bframes = Number(bframesSelect.value || "0");
      const lookahead = Number(lookaheadInput.value || "0");
      const adaptiveAllowed = lookahead > 0 && bframes > 0;
      if (!adaptiveAllowed) {
        adaptiveBframesSelect.value = "0";
      }
      setLabelDisabled(adaptiveBframesSelect.closest("label"), !adaptiveAllowed);
    }

    function updateRateControlState() {
      const rc = profileRcSelect.value;
      document.querySelectorAll("[data-rc-field]").forEach((label) => {
        const field = label.dataset.rcField;
        const enable =
          (rc === "cq" && field === "cq") ||
          (rc !== "cq" && ["bitrate", "maxrate", "bufsize"].includes(field));
        setLabelDisabled(label, !enable);
      });
    }

    function updateFfmpegPreview() {
      if (!ffmpegPreview) return;

      let rc = profileRcSelect.value || "vbr";
      if (rc === "cbr") {
        rc = "vbr";
      }
      const resolution = profileResolutionSelect.value;
      const [, height = "720"] = String(resolution || "1280x720").split("x");
      const filterParts = [
        `scale_cuda=-2:${height}:force_original_aspect_ratio=decrease`,
        `fps=${profileFpsSelect.value || 30}`,
      ];

      const parts = [
        "ffmpeg",
        "-y",
        "-hwaccel cuda",
        "-hwaccel_output_format cuda",
        "-i input.mkv",
        `-vf ${filterParts.join(",")}`,
        "-c:v h264_nvenc",
        `-preset ${profilePresetSelect.value || "p6"}`,
        `-profile:v ${profileTierSelect.value}`,
        `-level ${profileLevelSelect.value}`,
      ];

      if (rc === "cq") {
        parts.push("-rc constqp", `-qp ${profileCqSelect.value || 18}`);
      } else {
        parts.push(`-rc ${rc}`);
        parts.push(`-b:v ${profileBitrateSelect.value || "5M"}`);
        parts.push(`-maxrate ${profileMaxrateSelect.value || "10M"}`);
        parts.push(`-bufsize ${profileBufsizeSelect.value || "16M"}`);
        if (rc === "vbr_hq") {
          parts.push("-multipass fullres");
        }
      }

      const bframes = Number(bframesSelect.value || "0");
      parts.push(`-bf ${bframes}`);

      const lookahead = Number(lookaheadInput.value || "0");
      if (lookahead > 0) {
        parts.push(`-rc-lookahead ${lookahead}`);
      } else {
        parts.push("-rc-lookahead 0");
      }

      const adaptiveAllowed = lookahead > 0 && bframes > 0;
      const adaptive = adaptiveAllowed && adaptiveBframesSelect.value === "1";
      parts.push(`-b_adapt ${adaptive ? 1 : 0}`);

      const aqEnabled = aqSelect.value === "1";
      const spatial = aqEnabled && spatialAqSelect.value === "1";
      const temporal = aqEnabled && temporalAqSelect.value === "1";
      parts.push(`-spatial_aq ${spatial ? 1 : 0}`, `-temporal_aq ${temporal ? 1 : 0}`);
      if (aqEnabled && aqStrengthInput) {
        parts.push("-aq-strength", aqStrengthInput.value || "7");
      }

      parts.push("-movflags +faststart");
      parts.push("-c:a aac", `-b:a ${audioBitrateSelect.value || "192k"}`, "-ac 2");
      parts.push("output.mp4");

      ffmpegPreview.textContent = parts.join(" ");
    }

    function profileList() {
      if (!configCache) return [];
      const raw = configCache.profiles || [];
      if (Array.isArray(raw)) return raw;
      return Object.entries(raw).map(([name, profile]) => ({ ...profile, name }));
    }

    function libraryList() {
      if (!configCache || !configCache.libraries) return [];
      return Object.entries(configCache.libraries).map(([name, library]) => ({ name, ...library }));
    }

    function findProfile(name) {
      return profileList().find((profile) => profile.name === name);
    }

    function renderProfileSelect() {
      const profileSelect = document.querySelector("#profile-select");
      const previousValue = profileSelect.value;
      const profiles = profileList();
      profileSelect.innerHTML = "";
      if (!profiles.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No profiles available";
        profileSelect.appendChild(option);
        profileNameInput.value = "";
        return;
      }
      profiles.forEach((profile) => {
        const option = document.createElement("option");
        option.value = profile.name;
        option.textContent = profile.id ? `${profile.name} (#${profile.id})` : profile.name;
        profileSelect.appendChild(option);
      });
      if (previousValue && profiles.some((profile) => profile.name === previousValue)) {
        profileSelect.value = previousValue;
      } else if (!profileSelect.value) {
        profileSelect.value = profiles[0].name;
      }
      profileNameInput.value = profileSelect.value;

      if (libraryProfileSelect) {
        libraryProfileSelect.innerHTML = "";
        const previousLibrarySelection = libraryProfileSelect.value;
        profiles.forEach((profile) => {
          const option = document.createElement("option");
          option.value = profile.id ?? profile.name;
          option.textContent = profile.name;
          libraryProfileSelect.appendChild(option);
        });
        if (previousLibrarySelection && profiles.some((profile) => String(profile.id ?? profile.name) === previousLibrarySelection)) {
          libraryProfileSelect.value = previousLibrarySelection;
        } else if (!libraryProfileSelect.value && profiles.length) {
          libraryProfileSelect.value = profiles[0].id ?? profiles[0].name;
        }
      }
    }

    function renderLibraryProfiles() {
      if (!libraryProfiles) return;
      const libraries = libraryList();
      const profiles = profileList();
      libraryProfiles.innerHTML = "";
      if (!libraries.length) {
        const message = document.createElement("p");
        message.classList.add("hint");
        message.textContent = "No libraries configured.";
        libraryProfiles.appendChild(message);
        return;
      }
      libraries.forEach((library) => {
        const row = document.createElement("div");
        row.classList.add("grid-two", "library-row");
        row.dataset.library = library.name;

        const info = document.createElement("div");
        const displayRoot = normalizeDisplayPath(library.root);
        info.innerHTML = `<strong>${library.name}</strong><p class="hint" style="margin:0">${displayRoot} (depth ${library.depth || "max"})</p>`;

        const controls = document.createElement("div");
        controls.style.display = "flex";
        controls.style.gap = "0.5rem";
        controls.style.alignItems = "center";

        const select = document.createElement("select");
        select.dataset.library = library.name;
        profiles.forEach((profile) => {
          const option = document.createElement("option");
          option.value = profile.id;
          option.textContent = profile.name;
          select.appendChild(option);
        });
        if (library.profile_id) {
          select.value = library.profile_id;
        }

        const button = document.createElement("button");
        button.type = "button";
        button.dataset.library = library.name;
        button.dataset.action = "update";
        button.textContent = "Update profile";

        const remove = document.createElement("button");
        remove.type = "button";
        remove.dataset.library = library.name;
        remove.dataset.action = "remove";
        remove.classList.add("secondary");
        remove.textContent = "Remove";

        const status = document.createElement("span");
        status.classList.add("hint", "library-status");

        controls.appendChild(select);
        controls.appendChild(button);
        controls.appendChild(remove);
        controls.appendChild(status);

        row.appendChild(info);
        row.appendChild(controls);
        libraryProfiles.appendChild(row);
      });
    }

    function renderLibraryFilters() {
      if (!entryLibraryFilter) return;
      const libraries = libraryList();
      const previous = entryLibraryFilter.value;
      entryLibraryFilter.innerHTML = "";
      const all = document.createElement("option");
      all.value = "";
      all.textContent = "All libraries";
      entryLibraryFilter.appendChild(all);
      libraries.forEach((library) => {
        const option = document.createElement("option");
        option.value = library.name;
        option.textContent = library.name;
        entryLibraryFilter.appendChild(option);
      });
      if (previous && libraries.some((library) => library.name === previous)) {
        entryLibraryFilter.value = previous;
      }
    }

    function switchPage(pageName) {
      pages.forEach((page) => {
        page.classList.toggle("active", page.dataset.page === pageName);
      });
      navLinks.forEach((link) => {
        link.classList.toggle("active", link.dataset.page === pageName);
      });
      if (window.location.hash !== `#${pageName}`) {
        window.location.hash = `#${pageName}`;
      }
    }

    navLinks.forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        switchPage(event.currentTarget.dataset.page);
      });
    });

    window.addEventListener("hashchange", () => {
      const target = window.location.hash.replace("#", "") || "queue";
      switchPage(target);
    });

    async function fetchConfig() {
      const response = await fetch(`/api/config?ts=${Date.now()}`, { cache: "no-store" });
      const config = await response.json();
      configCache = config;
      isWsl2 = Boolean(config.environment?.is_wsl2);
      logRetention.value = config.logging?.retention_days ?? 7;
      renderProfileSelect();
      const profileSelect = document.querySelector("#profile-select");
      if (profileSelect.value) {
        loadProfile(profileSelect.value);
      }
      renderLibraryProfiles();
      renderLibraryFilters();
    }

    function loadProfile(name) {
      if (!configCache) return;
      const profile = findProfile(name);
      if (!profile) return;
      document.querySelector("#profile-select").value = name;
      profileNameInput.value = name;
      ensureOption(profileTierSelect, profile.profile);
      ensureOption(profileLevelSelect, profile.level);
      ensureOption(profileResolutionSelect, profile.max_resolution || profile.resolution);
      ensureOption(profileFpsSelect, profile.max_fps ?? 30);
      ensureOption(profilePresetSelect, profile.preset ?? "p7");
      let rcMode = profile.rc || "vbr";
      if (rcMode === "cbr") {
        rcMode = "vbr";
      }
      ensureOption(profileRcSelect, rcMode);
      ensureOption(profileCqSelect, profile.cq ?? 18);
      ensureOption(profileBitrateSelect, profile.bitrate ?? profile.max_bitrate ?? "5M");
      ensureOption(profileMaxrateSelect, profile.max_bitrate ?? "10M");
      ensureOption(profileBufsizeSelect, profile.bufsize ?? "16M");
      ensureOption(bframesSelect, profile.bframes ?? 2);
      lookaheadInput.value = profile.lookahead ?? 24;
      adaptiveBframesSelect.value = profile.adaptive_b_frames ? "1" : "0";
      aqSelect.value = profile.aq === false ? "0" : "1";
      spatialAqSelect.value = profile.spatial_aq === false ? "0" : "1";
      temporalAqSelect.value = profile.temporal_aq === false ? "0" : "1";
      if (aqStrengthInput) {
        const strength = profile.aq_strength ?? 7;
        aqStrengthInput.value = String(strength);
        aqStrengthValue.textContent = String(strength);
      }
      ensureOption(audioBitrateSelect, profile.audio?.bitrate ?? "192k");

      syncLookaheadDisplay();
      enforceLevelConstraints();
      updateRateControlState();
      updateBframeState();
      updateAqState();
      updateFfmpegPreview();
    }

    function renderJobTable() {
      const sorted = [...(jobCache || [])].sort((a, b) => {
        const statusA = String(a.status || "").toLowerCase();
        const statusB = String(b.status || "").toLowerCase();
        if (statusA === "running" && statusB !== "running") return -1;
        if (statusB === "running" && statusA !== "running") return 1;
        const createdA = new Date(a.created_at || 0).getTime();
        const createdB = new Date(b.created_at || 0).getTime();
        if (createdA === createdB) return 0;
        return createdA > createdB ? -1 : 1;
      });
      jobRows.innerHTML = "";
      sorted.forEach((job) => {
        const tr = document.createElement("tr");
        const progress = job.progress ?? 0;
        const status = String(job.status || "unknown").toLowerCase();
        const statusLabel = status ? `${status[0].toUpperCase()}${status.slice(1)}` : "";
        const normalizedPath = normalizeDisplayPath(job.path);
        const fileName = fileNameFromPath(normalizedPath);
        const recordedElapsed = Number(job?.elapsed_seconds ?? Number.NaN);
        const elapsedSeconds =
          status === "running"
            ? jobElapsedSeconds(job)
            : Number.isFinite(recordedElapsed)
            ? recordedElapsed
            : 0;
        const elapsed = formatElapsed(elapsedSeconds);
        tr.innerHTML = `
          <td>
            <button
              type="button"
              class="link-button job-link"
              data-job-id="${job.id}"
              data-job-path="${normalizedPath}"
            >
              ${job.id.slice(0, 8)}
            </button>
          </td>
          <td class="status-${status}">${statusLabel}</td>
          <td>${elapsed}</td>
          <td class="path-cell" title="${normalizedPath}">${fileName}</td>
          <td>${job.profile}</td>
          <td>${progress}%</td>
        `;
        jobRows.appendChild(tr);
      });
    }

    function upsertJob(job) {
      if (!job) return;
      const existingIndex = jobCache.findIndex((item) => item.id === job.id);
      if (existingIndex >= 0) {
        jobCache[existingIndex] = { ...jobCache[existingIndex], ...job };
      } else {
        jobCache.unshift(job);
      }
      renderJobTable();
    }

    async function fetchJobs() {
      const response = await fetch("/api/jobs");
      jobCache = await response.json();
      renderJobTable();
    }

    async function enqueueLibraryScan(library) {
      scanResult.textContent = `Enqueuing ${library} scan...`;
      const response = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ library }),
      });
      const result = await response.json();
      if (response.ok) {
        scanResult.textContent = `Scan scheduled for: ${result.scheduled.join(", ")}`;
      } else {
        scanResult.textContent = `Scan failed: ${result.detail || "Unknown error"}`;
      }
    }

    async function refreshQueueState() {
      const response = await fetch("/api/queue/state");
      const state = await response.json();
      const depth = Number(state.depth ?? 0);
      const inFlight = Number(state.pending ?? 0);
      const pending = Math.max(0, depth - inFlight);
      queueMetrics.textContent = `Depth: ${depth} (pending ${pending})`;
      queueMetrics.className = "status-pill status-idle";
      const workerMetrics = state.workers || {};
      if (gpuMetrics) {
        const workerCount = workerMetrics.workers ?? 0;
        const available = workerMetrics.available ?? 0;
        gpuMetrics.textContent = `GPU: ${available}/${workerCount || 0} ready`;
        gpuMetrics.className = "status-pill status-idle";
        if (!workerCount || !available) {
          gpuMetrics.className = "status-pill status-paused";
        }
      }
      if (state.paused) {
        queueStatus.textContent = "Paused";
        queueStatus.className = "status-pill status-paused";
        pauseToggle.textContent = "Resume";
      } else {
        queueStatus.textContent = "Running";
        queueStatus.className = "status-pill status-running";
        pauseToggle.textContent = "Pause";
      }
    }

    function formatLogTimestamp(rawTimestamp) {
      const parsed = new Date(rawTimestamp);
      if (Number.isNaN(parsed.getTime())) {
        return rawTimestamp;
      }
      return parsed.toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "medium",
      });
    }

    function severityClass(value) {
      const normalized = (value || "").toLowerCase();
      if (normalized === "warning") return "severity-warning";
      if (normalized === "error" || normalized === "critical") return "severity-error";
      if (normalized === "verbose" || normalized === "debug") return "severity-verbose";
      return "severity-info";
    }

    async function fetchLogs() {
      const params = new URLSearchParams();
      if (logSource.value) params.set("source", logSource.value);
      if (logCategory.value) params.set("category", logCategory.value);
      if (logLevel.value) params.set("min_severity", logLevel.value);
      if (logQuery.value) params.set("query", logQuery.value);
      const response = await fetch(`/api/logs?${params.toString()}`);
      const entries = await response.json();
      logList.innerHTML = "";
      entries.forEach((entry) => {
        const container = document.createElement("div");
        container.className = "log-entry";
        const severity = entry.severity || entry.level;
        container.innerHTML = `
          <div class="log-meta">
            <span class="log-pill ${severityClass(severity)}">${severity}</span>
            <span class="log-pill pill-muted">${entry.source || entry.logger}</span>
            <span class="log-pill pill-outline">${entry.category || entry.logger}</span>
            <span class="log-timestamp log-pill pill-outline">${formatLogTimestamp(entry.timestamp)}</span>
          </div>
          <div class="log-message">${entry.message}</div>
        `;
        logList.appendChild(container);
      });
    }

    function summarizeEntries(entries) {
      const summary = {
        pending: 0,
        converting: 0,
        converted: 0,
        failed: 0,
      };
      entries.forEach((entry) => {
        if (summary[entry.status] !== undefined) {
          summary[entry.status] += 1;
        }
      });
      return summary;
    }

    function upsertLibraryEntry(update) {
      if (!update) return;
      const existing = libraryEntries.find((entry) => entry.id === update.id);
      const map = new Map(libraryEntries.map((entry) => [entry.id, entry]));
      map.set(update.id, update);
      libraryEntries = Array.from(map.values()).sort((a, b) =>
        new Date(b.updated_at || 0) - new Date(a.updated_at || 0),
      );
      if (!existing && entryTotal !== null) {
        entryTotal += 1;
      }
      renderEntrySummary();
      renderEntryTable();
    }

    function formatStatusChip(status) {
      const normalized = status || "unknown";
      return `<span class="status-chip status-${normalized}">${normalized}</span>`;
    }

    function formatProfile(entry) {
      const parts = [entry.profile];
      if (entry.profile_id) parts.push(`#${entry.profile_id}`);
      return `<span class="profile-chip">${parts.join(" ")}</span>`;
    }

    function formatUpdated(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return date.toLocaleString();
    }

    function filteredEntries() {
      const term = (entrySearch.value || "").toLowerCase();
      const library = entryLibraryFilter.value;
      const status = entryStatusFilter.value;
      return libraryEntries.filter((entry) => {
        if (library && entry.library !== library) return false;
        if (status && entry.status !== status) return false;
        if (!term) return true;
        const normalizedPath = normalizeDisplayPath(entry.path).toLowerCase();
        return (
          normalizedPath.includes(term) ||
          entry.library.toLowerCase().includes(term)
        );
      });
    }

    function renderEntrySummary() {
      const summary = summarizeEntries(libraryEntries);
      const entries = [
        { label: "Pending", key: "pending" },
        { label: "Converting", key: "converting" },
        { label: "Converted", key: "converted" },
        { label: "Failed", key: "failed" },
      ];
      entrySummary.innerHTML = "";
      entries.forEach((item) => {
        const card = document.createElement("div");
        card.className = "stat-card";
        card.innerHTML = `
          <h4>${item.label}</h4>
          <div class="stat-value">${summary[item.key] ?? 0}</div>
        `;
        entrySummary.appendChild(card);
      });
    }

    function renderEntryTable() {
      const entries = filteredEntries();
      entryRows.innerHTML = "";
      entries.forEach((entry) => {
        const tr = document.createElement("tr");
        const normalizedPath = normalizeDisplayPath(entry.path);
        const fileName = fileNameFromPath(normalizedPath);
        tr.innerHTML = `
          <td>${entry.id}</td>
          <td>${formatStatusChip(entry.status)}</td>
          <td>${entry.library}</td>
          <td class="path-cell" title="${normalizedPath}">${fileName}</td>
          <td>${formatProfile(entry)}</td>
          <td>${formatUpdated(entry.updated_at)}</td>
          <td>
            <div class="row-actions">
              <button type="button" data-action="reprocess" data-entry-id="${entry.id}">Reprocess</button>
              <button type="button" class="secondary" data-action="remove" data-entry-id="${entry.id}">Delete original</button>
              <button type="button" class="secondary" data-action="logs" data-entry-id="${entry.id}">View logs</button>
            </div>
          </td>
        `;
        tr.dataset.entryId = entry.id;
        tr.dataset.jobId = entry.last_job_id || "";
        tr.dataset.path = normalizedPath;
        entryRows.appendChild(tr);
      });
      const loaded = libraryEntries.length;
      const totalText = entryTotal !== null ? `${loaded}/${entryTotal}` : `${loaded}`;
      const moreNote = entryHasMore ? " (more available)" : "";
      entryPageIndicator.textContent = `Showing ${entries.length} entries (loaded ${totalText})${moreNote}`;
      if (entryLoadMore) {
        entryLoadMore.disabled = entryLoadingState || !entryHasMore;
      }
      if (entryLoading) {
        entryLoading.textContent = entryLoadingState ? "Loading entries…" : "";
      }
    }

    async function loadLibraryEntries({ reset = false } = {}) {
      if (entryLoadingState) return;
      entryLoadingState = true;
      if (entryLoading) entryLoading.textContent = "Loading entries…";
      if (reset) {
        entryOffset = 0;
        entryTotal = null;
        entryHasMore = true;
        libraryEntries = [];
      }

      const limit = Number(entryPageSize.value || "20");
      const params = new URLSearchParams({
        limit: String(limit),
        offset: String(entryOffset),
        include_total: "true",
      });
      if (entryStatusFilter.value) params.set("status", entryStatusFilter.value);
      if (entryLibraryFilter.value) params.set("library", entryLibraryFilter.value);

      try {
        const response = await fetch(`/api/library/entries?${params.toString()}`);
        const result = await response.json();
        const items = Array.isArray(result) ? result : result.items || [];
        if (result.total !== undefined && result.total !== null) {
          entryTotal = result.total;
        }
        const received = items.length;
        const map = new Map(libraryEntries.map((entry) => [entry.id, entry]));
        items.forEach((item) => map.set(item.id, item));
        libraryEntries = Array.from(map.values()).sort((a, b) =>
          new Date(b.updated_at || 0) - new Date(a.updated_at || 0),
        );
        entryOffset += result.limit ?? limit;
        if (entryTotal !== null) {
          entryHasMore = entryOffset < entryTotal;
        } else {
          entryHasMore = received === limit;
        }
        renderEntrySummary();
        renderEntryTable();
        libraryStatus.textContent = `Loaded ${libraryEntries.length} entr${
          libraryEntries.length === 1 ? "y" : "ies"
        }`;
      } catch (error) {
        libraryStatus.textContent = "Failed to load entries";
      } finally {
        entryLoadingState = false;
        if (entryLoading) entryLoading.textContent = "";
      }
    }

    async function refreshLibraryEntries() {
      await loadLibraryEntries({ reset: true });
    }

    async function handleEntryAction(action, entryId, button) {
      if (!entryId) return;
      if (action === "logs") {
        const row = button.closest("tr");
        const jobId = row?.dataset.jobId;
        const path = row?.dataset.path;
        logQuery.value = jobId || path || "";
        switchPage("logs");
        fetchLogs();
        return;
      }
      const endpoint =
        action === "reprocess"
          ? `/api/library/entries/${entryId}/reprocess`
          : `/api/library/entries/${entryId}/remove-original`;
      const label = button.textContent;
      button.textContent = "Working...";
      button.disabled = true;
      try {
        const response = await fetch(endpoint, { method: "POST" });
        if (!response.ok) {
          const detail = await response.json();
          alert(detail.detail || "Request failed");
        }
        await refreshLibraryEntries();
      } finally {
        button.textContent = label;
        button.disabled = false;
      }
    }

    async function fetchLogFilters() {
      const [categoriesResponse, sourcesResponse] = await Promise.all([
        fetch("/api/logs/categories"),
        fetch("/api/logs/sources"),
      ]);
      const categories = await categoriesResponse.json();
      const sources = await sourcesResponse.json();
      const existingCategory = logCategory.value;
      const existingSource = logSource.value;

      logCategory.innerHTML = "";
      const allCategories = document.createElement("option");
      allCategories.value = "";
      allCategories.textContent = "All categories";
      logCategory.appendChild(allCategories);
      categories.forEach((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        logCategory.appendChild(option);
      });
      if (existingCategory && categories.includes(existingCategory)) {
        logCategory.value = existingCategory;
      }

      logSource.innerHTML = "";
      const allSources = document.createElement("option");
      allSources.value = "";
      allSources.textContent = "All sources";
      logSource.appendChild(allSources);
      sources.forEach((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        logSource.appendChild(option);
      });
      if (existingSource && sources.includes(existingSource)) {
        logSource.value = existingSource;
      }
    }

    function formatBytes(bytes) {
      if (bytes === 0) return "0 B";
      const sizes = ["B", "KB", "MB", "GB", "TB"];
      const i = Math.floor(Math.log(bytes) / Math.log(1024));
      const value = bytes / 1024 ** i;
      return `${value.toFixed(value >= 10 ? 0 : 1)} ${sizes[i]}`;
    }

    async function fetchLogStats() {
      const response = await fetch("/api/logs/stats");
      const stats = await response.json();
      logStats.textContent = `Stored ${stats.total_entries} entr${
        stats.total_entries === 1 ? "y" : "ies"
      } (${formatBytes(stats.file_size_bytes)}) with a ${stats.retention_days}-day retention window.`;
      if (!logRetention.value) {
        logRetention.value = stats.retention_days;
      }
    }

    function connectLiveUpdates() {
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${protocol}://${window.location.host}/ws`);
      liveSocket = socket;

      socket.onopen = () => {
        libraryStatus.textContent = "Live updates connected";
      };

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data || "{}");
          if (data.type === "entry-update" && data.entry) {
            upsertLibraryEntry(data.entry);
          } else if (data.type === "job-update" && data.job) {
            upsertJob(data.job);
          } else if (data.type === "library-update") {
            fetchConfig();
            refreshLibraryEntries();
          }
        } catch (error) {
          console.error("Failed to process websocket message", error);
        }
      };

      socket.onclose = () => {
        libraryStatus.textContent = "Live updates disconnected; retrying...";
        setTimeout(() => {
          connectLiveUpdates();
        }, 2000);
      };

      socket.onerror = () => {
        socket.close();
      };
    }

    document
      .querySelector("#scan-movies")
      .addEventListener("click", () => enqueueLibraryScan("movies"));
    document
      .querySelector("#scan-series")
      .addEventListener("click", () => enqueueLibraryScan("series"));

    pauseToggle.addEventListener("click", async () => {
      const paused = pauseToggle.textContent === "Resume";
      if (paused) {
        await fetch("/api/queue/resume", { method: "POST" });
      } else {
        const reason = prompt("Reason for pausing? (optional)", "") || "";
        await fetch("/api/queue/pause", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason }),
        });
      }
      await refreshQueueState();
    });

    if (clearProcessedButton) {
      clearProcessedButton.addEventListener("click", async () => {
        clearProcessedButton.disabled = true;
        const originalMessage = scanResult.textContent;
        scanResult.textContent = "Clearing processed jobs...";
        try {
          const response = await fetch("/api/jobs/clear", { method: "POST" });
          const result = await response.json().catch(() => ({}));
          if (response.ok) {
            const removed = result.removed ?? 0;
            scanResult.textContent = removed
              ? `Removed ${removed} processed ${removed === 1 ? "job" : "jobs"}.`
              : "No processed jobs to clear.";
            fetchJobs();
          } else {
            scanResult.textContent = result.detail || "Unable to clear jobs";
          }
        } catch (error) {
          scanResult.textContent = "Unable to clear jobs";
        } finally {
          clearProcessedButton.disabled = false;
          setTimeout(() => {
            const message = scanResult.textContent || "";
            if (
              message.includes("Removed") ||
              message.includes("Unable") ||
              message.includes("No processed")
            ) {
              scanResult.textContent = originalMessage;
            }
          }, 4000);
        }
      });
    }

    document.querySelector("#refresh-logs").addEventListener("click", fetchLogs);
    logCategory.addEventListener("change", fetchLogs);
    logSource.addEventListener("change", fetchLogs);
    logLevel.addEventListener("change", fetchLogs);

    document.querySelector("#profile-select").addEventListener("change", (event) => {
      loadProfile(event.target.value);
      profileNameInput.value = event.target.value;
    });

    profileRcSelect.addEventListener("change", () => {
      updateRateControlState();
      updateFfmpegPreview();
    });

    profileResolutionSelect.addEventListener("change", () => {
      enforceLevelConstraints("resolution");
      updateFfmpegPreview();
    });

    profileFpsSelect.addEventListener("change", () => {
      enforceLevelConstraints("fps");
      updateFfmpegPreview();
    });

    profileTierSelect.addEventListener("change", () => {
      updateBframeState();
      updateFfmpegPreview();
    });

    bframesSelect.addEventListener("change", () => {
      updateBframeState();
      updateFfmpegPreview();
    });

    adaptiveBframesSelect.addEventListener("change", updateFfmpegPreview);

    lookaheadInput.addEventListener("input", () => {
      syncLookaheadDisplay();
      updateBframeState();
      updateFfmpegPreview();
    });

    aqSelect.addEventListener("change", () => {
      updateAqState();
      updateFfmpegPreview();
    });

    spatialAqSelect.addEventListener("change", updateFfmpegPreview);
    temporalAqSelect.addEventListener("change", updateFfmpegPreview);
    if (aqStrengthInput) {
      aqStrengthInput.addEventListener("input", () => {
        aqStrengthValue.textContent = aqStrengthInput.value;
        updateFfmpegPreview();
      });
    }

    [
      profileCqSelect,
      profileBitrateSelect,
      profileMaxrateSelect,
      profileBufsizeSelect,
      profilePresetSelect,
      profileLevelSelect,
      audioBitrateSelect,
    ].forEach((element) => {
      element.addEventListener("change", (event) => {
        if (event.target === profileLevelSelect) {
          enforceLevelConstraints("level");
        }
        updateFfmpegPreview();
      });
    });

    document.querySelector("#log-config-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const retentionDays = Number(logRetention.value || "7");
      logConfigResult.textContent = "Saving...";
      const response = await fetch("/api/config/logging", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days: retentionDays }),
      });
      const result = await response.json();
      if (response.ok) {
        logConfigResult.textContent = `Retention updated to ${result.retention_days} days.`;
        if (configCache) {
          configCache.logging = configCache.logging || {};
          configCache.logging.retention_days = result.retention_days;
          if (result.revision) {
            configCache.revision = result.revision;
          }
        }
        fetchLogStats();
        fetchLogs();
      } else {
        logConfigResult.textContent = `Save failed: ${result.detail || "Unknown error"}`;
      }
    });

    document.querySelector("#config-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const profileName = (profileNameInput.value || document.querySelector("#profile-select").value).trim();
      if (!profileName) {
        configResult.textContent = "Profile name is required.";
        return;
      }
      const payload = {
        name: profileName,
        codec: "h264",
        profile: profileTierSelect.value,
        level: profileLevelSelect.value,
        resolution: profileResolutionSelect.value,
        max_fps: Number(profileFpsSelect.value || "30"),
        bitrate: profileBitrateSelect.value,
        max_bitrate: profileMaxrateSelect.value,
        bufsize: profileBufsizeSelect.value,
        preset: profilePresetSelect.value,
        rc: profileRcSelect.value,
        cq: Number(profileCqSelect.value || "18"),
        bframes: Number(bframesSelect.value || "0"),
        lookahead: Number(lookaheadInput.value || "0"),
        adaptive_b_frames: adaptiveBframesSelect.value === "1",
        aq: aqSelect.value === "1",
        spatial_aq: spatialAqSelect.value === "1",
        temporal_aq: temporalAqSelect.value === "1",
        aq_strength: Number(aqStrengthInput.value || "7"),
        audio: {
          codec: "aac",
          bitrate: audioBitrateSelect.value,
          channels: 2,
        },
      };
      configResult.textContent = "Saving...";
      const response = await fetch("/api/config/encoding", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (response.ok) {
        configResult.textContent = `Updated profile ${result.profile?.name || profileName}`;
        await fetchConfig();
        loadProfile(profileName);
      } else {
        configResult.textContent = `Save failed: ${result.detail}`;
      }
    });

    libraryCreateForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const name = (libraryNameInput.value || "").trim();
      const path = (libraryPathInput.value || "").trim();
      const profileId = Number(libraryProfileSelect.value || "0");
      if (!name || !path || !Number.isFinite(profileId) || profileId <= 0) {
        libraryCreateResult.textContent = "Name, path, and profile are required.";
        return;
      }
      libraryCreateResult.textContent = "Adding library...";
      const response = await fetch("/api/libraries", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, root: path, profile_id: profileId }),
      });
      const result = await response.json();
      if (response.ok) {
        libraryCreateResult.textContent = `Added ${result.name}`;
        libraryNameInput.value = "";
        libraryPathInput.value = "";
        configCache = configCache || {};
        configCache.libraries = configCache.libraries || {};
        configCache.libraries[result.name] = {
          root: result.root,
          depth: result.depth,
          profile_id: result.profile_id,
          profile: result.profile,
        };
        renderLibraryProfiles();
        renderLibraryFilters();
        refreshLibraryEntries();
      } else {
        libraryCreateResult.textContent = result.detail || "Unable to add library";
      }
    });

    libraryProfiles.addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-library]");
      if (!button) return;
      const row = button.closest(".library-row");
      const select = row?.querySelector("select[data-library]");
      const status = row?.querySelector(".library-status");
      const action = button.dataset.action || "update";
      const libraryName = button.dataset.library;
      if (action === "remove") {
        if (!window.confirm(`Remove library ${libraryName}? Existing entries will be marked removed.`)) return;
        if (status) status.textContent = "Removing...";
        const response = await fetch(`/api/libraries/${libraryName}`, { method: "DELETE" });
        const result = await response.json().catch(() => ({}));
        if (response.ok) {
          if (status) status.textContent = "Removed";
          if (configCache?.libraries) {
            delete configCache.libraries[libraryName];
          }
          renderLibraryProfiles();
          renderLibraryFilters();
          refreshLibraryEntries();
        } else if (status) {
          status.textContent = result.detail || "Remove failed";
        }
        return;
      }

      if (!select) return;
      const profileId = Number(select.value);
      if (status) status.textContent = "Saving...";
      const response = await fetch(`/api/libraries/${libraryName}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_id: profileId }),
      });
      const result = await response.json();
      if (response.ok) {
        if (status) status.textContent = "Updated";
        configCache.libraries = configCache.libraries || {};
        configCache.libraries[libraryName] = {
          ...(configCache.libraries[libraryName] || {}),
          profile_id: profileId,
          profile: result.profile,
        };
      } else if (status) {
        status.textContent = result.detail || "Update failed";
      }
    });

    refreshEntriesBtn.addEventListener("click", refreshLibraryEntries);

    entrySearch.addEventListener("input", () => {
      renderEntryTable();
    });

    [entryLibraryFilter, entryStatusFilter, entryPageSize].forEach((control) => {
      control.addEventListener("change", () => {
        refreshLibraryEntries();
      });
    });

    entryLoadMore.addEventListener("click", () => {
      loadLibraryEntries();
    });

    entryRows.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const { action, entryId } = button.dataset;
      handleEntryAction(action, entryId, button);
    });

    jobRows.addEventListener("click", (event) => {
      const link = event.target.closest(".job-link");
      if (!link) return;
      const jobId = link.dataset.jobId || "";
      const jobPath = link.dataset.jobPath || "";
      logQuery.value = jobPath || jobId;
      switchPage("logs");
      fetchLogs();
    });

    fetchConfig();
    fetchJobs();
    fetchLogFilters();
    fetchLogs();
    fetchLogStats();
    refreshLibraryEntries();
    refreshQueueState();
    connectLiveUpdates();
    const initialPage = window.location.hash.replace("#", "") || "queue";
    switchPage(initialPage);
    setInterval(fetchJobs, 5000);
    setInterval(fetchLogs, 6000);
    setInterval(fetchLogFilters, 15000);
    setInterval(fetchLogStats, 20000);
    setInterval(refreshQueueState, 7000);
    setInterval(refreshLibraryEntries, 30000);
