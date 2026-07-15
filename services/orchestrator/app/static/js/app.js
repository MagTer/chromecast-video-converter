const navLinks = Array.from(document.querySelectorAll("nav a[data-page]"));
    const pages = Array.from(document.querySelectorAll(".page"));
    const jobRows = document.querySelector("#job-rows");
    const historyRows = document.querySelector("#history-rows");
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
    const copyLogsButton = document.querySelector("#copy-logs");
    const configResult = document.querySelector("#config-result");
    const logRetention = document.querySelector("#log-retention"); // Moved
    const logConfigResult = document.querySelector("#log-config-result"); // Moved
    const opsScanInterval = document.querySelector("#ops-scan-interval"); // Moved
    const opsConfigResult = document.querySelector("#ops-config-result"); // Moved
    const gpuSummary = document.querySelector("#gpu-summary");
    const cpuSummary = document.querySelector("#cpu-summary");
    const logStats = document.querySelector("#log-stats"); // Moved
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
    const clearProcessedButton = document.querySelector("#clear-processed");
    const ffmpegPreview = document.querySelector("#ffmpeg-preview");
    const cpuFfmpegPreview = document.querySelector("#cpu-ffmpeg-preview");
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
    const cpuProfileTierSelect = document.querySelector("#cpu-profile-tier");
    const cpuProfileLevelSelect = document.querySelector("#cpu-profile-level");
    const cpuProfileResolutionSelect = document.querySelector("#cpu-profile-resolution");
    const cpuProfileFpsSelect = document.querySelector("#cpu-profile-maxfps");
    const cpuProfilePresetSelect = document.querySelector("#cpu-profile-preset");
    const cpuProfileRcSelect = document.querySelector("#cpu-profile-rc");
    const cpuProfileCqSelect = document.querySelector("#cpu-profile-cq");
    const cpuProfileBitrateSelect = document.querySelector("#cpu-profile-bitrate");
    const cpuProfileMaxrateSelect = document.querySelector("#cpu-profile-maxrate");
    const cpuProfileBufsizeSelect = document.querySelector("#cpu-profile-bufsize");
    const cpuLookaheadInput = document.querySelector("#cpu-profile-lookahead");
    const cpuLookaheadValue = document.querySelector("#cpu-lookahead-value");
    const cpuBframesSelect = document.querySelector("#cpu-profile-bframes");
    const cpuAdaptiveBframesSelect = document.querySelector("#cpu-profile-adaptive-bframes");
    const cpuAudioBitrateSelect = document.querySelector("#cpu-profile-audio-bitrate");
    const reprocessAllBtn = document.querySelector("#reprocess-all");
    const deleteAllOriginalsBtn = document.querySelector("#delete-all-originals");
    const setupWizard = document.querySelector("#setup-wizard");
    const wizardSetupBtn = document.querySelector("#wizard-setup-btn");
    const wizardSkipBtn = document.querySelector("#wizard-skip-btn");
    const refreshHistoryBtn = document.querySelector("#refresh-history");
    const resetConfigBtn = document.querySelector("#reset-config");
    const purgeJobsBtn = document.querySelector("#purge-inactive-jobs");
    const purgeJobsResult = document.querySelector("#purge-jobs-result");
    const profileSelect = document.querySelector("#profile-select");
    const profileNameInput = document.querySelector("#profile-name");
    const newProfileBtn = document.querySelector("#new-profile");
    const deleteProfileBtn = document.querySelector("#delete-profile");
    const profileToolbarResult = document.querySelector("#profile-toolbar-result");
    const libraryRows = document.querySelector("#library-rows");
    const libraryForm = document.querySelector("#library-form");
    const libraryNameInput = document.querySelector("#library-name");
    const libraryRootInput = document.querySelector("#library-root");
    const libraryProfileSelect = document.querySelector("#library-profile");
    const libraryFormResult = document.querySelector("#library-form-result");
    const scanButtons = document.querySelector("#scan-buttons");

    let configCache = null;
    let selectedProfileId = null;
    let creatingProfile = false;
    let libraryEntries = [];
    let entryOffset = 0;
    let entryTotal = null;
    let entryLoadingState = false;
    let entryHasMore = true;
    let jobCache = [];
    let historyCache = [];
    let liveSocket = null;
    let isWsl2 = false;
    let logCacheEntries = [];

    if (!logLevel.value) {
      logLevel.value = "INFO";
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
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

    function formatPipeline(pipeline) {
      if (!pipeline) return "GPU/GPU/GPU";
      const decode = String(pipeline.decode_type || "gpu").toUpperCase();
      const scale = String(pipeline.scale_type || "gpu").toUpperCase();
      const encode = String(pipeline.encode_type || "gpu").toUpperCase();
      const attempt = pipeline.attempt;
      const maxAttempts = pipeline.max_attempts;
      const attemptText =
        Number.isFinite(attempt) && Number.isFinite(maxAttempts) && maxAttempts > 0
          ? ` (${attempt}/${maxAttempts})`
          : "";
      return `${decode}/${scale}/${encode}${attemptText}`;
    }

    function summarizeProfile(profile, label) {
      if (!profile) return "—";
      const resolution = profile.resolution || profile.max_resolution || "1280x720";
      const fps = profile.max_fps ?? 30;
      const rc = (profile.rc || "vbr").toUpperCase();
      const preset = profile.preset || "";
      const bitrate = profile.bitrate || profile.max_bitrate || "n/a";
      const maxrate = profile.max_bitrate || profile.bitrate || "n/a";
      const encoder = label === "CPU" ? "libx264" : "h264_nvenc";
      return `${label || ""} ${encoder} · ${resolution} @ ${fps}fps\nRC ${rc} · preset ${preset}\nBitrate ${bitrate} (max ${maxrate})`;
    }

    function jobElapsedSeconds(job) {
      if (Number.isFinite(job?.elapsed_seconds)) {
        return job.elapsed_seconds;
      }
      const status = String(job?.status || "").toLowerCase();
      if (status === "pending") {
        return 0;
      }
      const startTimeStr = job?.started_at;
      const start = startTimeStr ? new Date(startTimeStr) : null;

      if (!start || Number.isNaN(start.getTime())) {
        return 0;
      }
      const updated = job?.updated_at ? new Date(job.updated_at) : null;
      const endDate =
        status === "completed" || status === "failed"
          ? updated && !Number.isNaN(updated.getTime())
            ? updated
            : start
          : new Date();
      const elapsedMs = endDate.getTime() - start.getTime();
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

    function enforceLevelConstraintsFor(selects, trigger = "load") {
      if (!selects) return;
      const { resolutionSelect, fpsSelect, levelSelect } = selects;
      if (!resolutionSelect || !fpsSelect || !levelSelect) return;
      const preferredResolution = resolutionSelect.value;
      const preferredFps = Number(fpsSelect.value || "30");
      const selectedLevel = parseFloat(levelSelect.value || "0") || 0;
      const requiredLevel = minimumLevelFor(preferredResolution, preferredFps);

      let targetLevel = selectedLevel || requiredLevel;
      if (trigger !== "level" && targetLevel < requiredLevel) {
        targetLevel = requiredLevel;
        const levelOption = Array.from(levelSelect.options).find(
          (option) => parseFloat(option.value) >= requiredLevel,
        );
        if (levelOption) {
          levelSelect.value = levelOption.value;
        }
      }

      const fpsOptions = Array.from(fpsSelect.options).map((option) => ({
        value: Number(option.value),
        option,
      }));
      const resolutionOptions = Array.from(resolutionSelect.options);

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
          resolutionSelect.value = resolution;
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
          fpsSelect.value = String(fps);
        }
      }

      const minimumLevel = minimumLevelFor(resolution, fps);
      Array.from(levelSelect.options).forEach((option) => {
        option.disabled = parseFloat(option.value) < minimumLevel;
      });
      if (parseFloat(levelSelect.value) < minimumLevel) {
        const bumpedLevel =
          Array.from(levelSelect.options).find(
            (option) => parseFloat(option.value) >= minimumLevel,
          )?.value || levelSelect.value;
        levelSelect.value = bumpedLevel;
        targetLevel = parseFloat(bumpedLevel) || targetLevel;
      }

      if (trigger === "level") {
        const stillTooHigh =
          minimumLevelFor(resolutionSelect.value, Number(fpsSelect.value || "0")) > targetLevel;
        if (stillTooHigh) {
          const fallback = pickClosestFps(allowedFpsValues, Number(fpsSelect.value || preferredFps));
          if (fallback !== null && fallback !== undefined) {
            fpsSelect.value = String(fallback);
          }
        }
      }
    }

    function enforceLevelConstraints(trigger = "load") {
      enforceLevelConstraintsFor(
        {
          resolutionSelect: profileResolutionSelect,
          fpsSelect: profileFpsSelect,
          levelSelect: profileLevelSelect,
        },
        trigger,
      );
    }

    function enforceCpuLevelConstraints(trigger = "load") {
      enforceLevelConstraintsFor(
        {
          resolutionSelect: cpuProfileResolutionSelect,
          fpsSelect: cpuProfileFpsSelect,
          levelSelect: cpuProfileLevelSelect,
        },
        trigger,
      );
    }

    function syncLookaheadDisplay() {
      if (lookaheadValue && lookaheadInput) {
        lookaheadValue.textContent = lookaheadInput.value || "0";
      }
      if (cpuLookaheadValue && cpuLookaheadInput) {
        cpuLookaheadValue.textContent = cpuLookaheadInput.value || "0";
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

    function updateBframeStateFor({
      tierSelect,
      bframesSelect: targetBframes,
      lookaheadInput: targetLookahead,
      adaptiveSelect,
    }) {
      if (!tierSelect || !targetBframes || !targetLookahead || !adaptiveSelect) return;
      const requiresZero = tierSelect.value === "baseline";
      if (requiresZero) {
        targetBframes.value = "0";
      }
      setLabelDisabled(targetBframes.closest("label"), requiresZero);

      const bframes = Number(targetBframes.value || "0");
      const lookahead = Number(targetLookahead.value || "0");
      const adaptiveAllowed = lookahead > 0 && bframes > 0;
      if (!adaptiveAllowed) {
        adaptiveSelect.value = "0";
      }
      setLabelDisabled(adaptiveSelect.closest("label"), !adaptiveAllowed);
    }

    function updateBframeState() {
      updateBframeStateFor({
        tierSelect: profileTierSelect,
        bframesSelect,
        lookaheadInput,
        adaptiveSelect: adaptiveBframesSelect,
      });
    }

    function updateCpuBframeState() {
      updateBframeStateFor({
        tierSelect: cpuProfileTierSelect,
        bframesSelect: cpuBframesSelect,
        lookaheadInput: cpuLookaheadInput,
        adaptiveSelect: cpuAdaptiveBframesSelect,
      });
    }

    function updateRateControlStateFor({ rcSelect, group, qualityField }) {
      if (!rcSelect || !group) return;
      const rc = rcSelect.value;
      document.querySelectorAll(`[data-rc-group="${group}"]`).forEach((label) => {
        const field = label.dataset.rcField;
        if (!field) return;
        const enable =
          (rc === qualityField && field === qualityField) ||
          (rc !== qualityField && ["bitrate", "maxrate", "bufsize"].includes(field));
        setLabelDisabled(label, !enable);
      });
    }

    function updateRateControlState() {
      updateRateControlStateFor({
        rcSelect: profileRcSelect,
        group: "gpu",
        qualityField: "cq",
      });
    }

    function updateCpuRateControlState() {
      updateRateControlStateFor({
        rcSelect: cpuProfileRcSelect,
        group: "cpu",
        qualityField: "crf",
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

    function updateCpuFfmpegPreview() {
      if (!cpuFfmpegPreview) return;
      if (!cpuProfileRcSelect || !cpuProfileResolutionSelect || !cpuProfileFpsSelect) return;

      let rc = cpuProfileRcSelect.value || "crf";
      const resolution = cpuProfileResolutionSelect.value;
      const [, height = "720"] = String(resolution || "1280x720").split("x");
      const filterParts = [
        `scale=-2:${height}`,
        `fps=${cpuProfileFpsSelect.value || 30}`,
      ];

      const parts = [
        "ffmpeg",
        "-y",
        "-i input.mkv",
        `-vf ${filterParts.join(",")}`,
        "-c:v libx264",
        `-preset ${cpuProfilePresetSelect.value || "slow"}`,
        `-profile:v ${cpuProfileTierSelect.value}`,
        `-level ${cpuProfileLevelSelect.value}`,
      ];

      if (rc === "crf") {
        parts.push(`-crf ${cpuProfileCqSelect.value || 20}`);
      } else {
        parts.push(`-b:v ${cpuProfileBitrateSelect.value || "5M"}`);
        parts.push(`-maxrate ${cpuProfileMaxrateSelect.value || "8M"}`);
        parts.push(`-bufsize ${cpuProfileBufsizeSelect.value || "16M"}`);
      }

      const bframes = Number(cpuBframesSelect.value || "0");
      parts.push(`-bf ${bframes}`);

      const lookahead = Number(cpuLookaheadInput.value || "0");
      if (lookahead > 0) {
        parts.push(`-rc-lookahead ${lookahead}`);
      }

      const adaptiveAllowed = lookahead > 0 && bframes > 0;
      const adaptive = adaptiveAllowed && cpuAdaptiveBframesSelect.value === "1";
      parts.push(`-b_adapt ${adaptive ? 1 : 0}`);

      parts.push("-movflags +faststart");
      parts.push("-c:a aac", `-b:a ${cpuAudioBitrateSelect.value || "192k"}`, "-ac 2");
      parts.push("output.mp4");

      cpuFfmpegPreview.textContent = parts.join(" ");
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

    function findProfileById(profileId) {
      if (profileId === null || profileId === undefined) return null;
      return profileList().find((profile) => Number(profile.id) === Number(profileId)) || null;
    }

    function renderProfileSelect() {
      if (!profileSelect) return;
      const profiles = profileList();
      profileSelect.innerHTML = "";
      profiles.forEach((profile) => {
        const option = document.createElement("option");
        option.value = String(profile.id);
        option.textContent = profile.name;
        profileSelect.appendChild(option);
      });
      if (creatingProfile) {
        profileSelect.selectedIndex = -1;
      } else if (selectedProfileId !== null && findProfileById(selectedProfileId)) {
        profileSelect.value = String(selectedProfileId);
      }
    }

    function renderProfileOptionsInto(select, selectedId) {
      if (!select) return;
      select.innerHTML = "";
      profileList().forEach((profile) => {
        const option = document.createElement("option");
        option.value = String(profile.id);
        option.textContent = profile.name;
        select.appendChild(option);
      });
      if (selectedId !== null && selectedId !== undefined) {
        select.value = String(selectedId);
      }
    }

    function renderLibraryTable() {
      if (!libraryRows) return;
      libraryRows.innerHTML = "";
      libraryList().forEach((library) => {
        const tr = document.createElement("tr");

        const nameCell = document.createElement("td");
        nameCell.textContent = library.name;
        tr.appendChild(nameCell);

        const rootCell = document.createElement("td");
        rootCell.className = "path-cell";
        rootCell.textContent = normalizeDisplayPath(library.root);
        rootCell.title = library.root || "";
        tr.appendChild(rootCell);

        const profileCell = document.createElement("td");
        const select = document.createElement("select");
        renderProfileOptionsInto(select, library.profile_id);
        select.addEventListener("change", async () => {
          select.disabled = true;
          try {
            const response = await fetch(`/api/libraries/${encodeURIComponent(library.name)}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ profile_id: Number(select.value) }),
            });
            if (!response.ok) {
              const detail = await response.json().catch(() => ({}));
              alert(detail.detail || "Failed to update library profile");
            }
            await fetchConfig();
          } finally {
            select.disabled = false;
          }
        });
        profileCell.appendChild(select);
        tr.appendChild(profileCell);

        const actionsCell = document.createElement("td");
        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "secondary danger";
        removeBtn.textContent = "Delete";
        removeBtn.addEventListener("click", async () => {
          if (
            !confirm(
              `Delete library "${library.name}"? Tracked entries are marked as removed; files on disk are not touched.`,
            )
          ) {
            return;
          }
          removeBtn.disabled = true;
          try {
            const response = await fetch(`/api/libraries/${encodeURIComponent(library.name)}`, {
              method: "DELETE",
            });
            if (!response.ok) {
              const detail = await response.json().catch(() => ({}));
              alert(detail.detail || "Failed to delete library");
            }
            await fetchConfig();
            refreshLibraryEntries();
          } finally {
            removeBtn.disabled = false;
          }
        });
        actionsCell.appendChild(removeBtn);
        tr.appendChild(actionsCell);

        libraryRows.appendChild(tr);
      });
    }

    function renderScanButtons() {
      if (!scanButtons) return;
      scanButtons.querySelectorAll("button[data-scan-library]").forEach((btn) => btn.remove());
      libraryList().forEach((library) => {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.scanLibrary = library.name;
        button.textContent = `Scan ${library.name}`;
        scanButtons.appendChild(button);
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

    function activePageName() {
      return document.querySelector(".page.active")?.dataset.page || "queue";
    }

    function pollWhen(pages, fn) {
      return () => {
        if (document.hidden) return;
        if (pages.length && !pages.includes(activePageName())) return;
        fn();
      };
    }

    function refreshPageData(pageName) {
      const refreshers = {
        queue: () => {
          fetchJobs();
          refreshQueueState();
        },
        history: () => fetchHistory(),
        logs: () => fetchLogs(),
        library: () => softRefreshLibraryEntries(),
        config: () => fetchLogStats(),
      };
      refreshers[pageName]?.();
    }

    function switchPage(pageName) {
      const changed = activePageName() !== pageName;
      pages.forEach((page) => {
        page.classList.toggle("active", page.dataset.page === pageName);
      });
      navLinks.forEach((link) => {
        link.classList.toggle("active", link.dataset.page === pageName);
      });
      if (window.location.hash !== `#${pageName}`) {
        window.location.hash = `#${pageName}`;
      }
      if (changed) {
        refreshPageData(pageName);
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
      // Initialize moved elements
      if (logRetention) {
        logRetention.value = config.logging?.retention_days ?? 7;
      }
      if (opsScanInterval) {
        opsScanInterval.value = config.operational?.scan_interval_min ?? 0;
      }
      
      const profiles = profileList();
      if (!creatingProfile) {
        const current = findProfileById(selectedProfileId) || profiles[0] || null;
        if (current) {
          selectedProfileId = current.id ?? null;
          loadProfile(current);
          if (profileNameInput) profileNameInput.value = current.name || "";
        }
      }
      renderProfileSelect();
      renderProfileOptionsInto(libraryProfileSelect, libraryProfileSelect?.value || null);
      renderLibraryTable();
      renderScanButtons();

      renderLibraryFilters();

      if (Object.keys(config.libraries || {}).length === 0) {
         if (setupWizard && !sessionStorage.getItem("wizardSkipped")) {
           setupWizard.showModal();
         }
      }
    }

    function loadProfile(profile) {
      if (!profile) return;
      const gpuProfile = profile.gpu || profile;
      const cpuProfile = profile.cpu || profile;
      // Removed document.querySelector("#profile-select").value = name;
      ensureOption(profileTierSelect, gpuProfile.profile);
      ensureOption(profileLevelSelect, gpuProfile.level);
      ensureOption(profileResolutionSelect, gpuProfile.resolution || gpuProfile.max_resolution);
      ensureOption(profileFpsSelect, gpuProfile.max_fps ?? 30);
      ensureOption(profilePresetSelect, gpuProfile.preset ?? "p7");
      let rcMode = gpuProfile.rc || "vbr";
      if (rcMode === "cbr") {
        rcMode = "vbr";
      }
      ensureOption(profileRcSelect, rcMode);
      ensureOption(profileCqSelect, gpuProfile.cq ?? 18);
      ensureOption(profileBitrateSelect, gpuProfile.bitrate ?? gpuProfile.max_bitrate ?? "5M");
      ensureOption(profileMaxrateSelect, gpuProfile.max_bitrate ?? "10M");
      ensureOption(profileBufsizeSelect, gpuProfile.bufsize ?? "16M");
      ensureOption(bframesSelect, gpuProfile.bframes ?? 2);
      lookaheadInput.value = gpuProfile.lookahead ?? 24;
      adaptiveBframesSelect.value = gpuProfile.adaptive_b_frames ? "1" : "0";
      aqSelect.value = gpuProfile.aq === false ? "0" : "1";
      spatialAqSelect.value = gpuProfile.spatial_aq === false ? "0" : "1";
      temporalAqSelect.value = gpuProfile.temporal_aq === false ? "0" : "1";
      if (aqStrengthInput) {
        const strength = gpuProfile.aq_strength ?? 7;
        aqStrengthInput.value = String(strength);
        aqStrengthValue.textContent = String(strength);
      }
      ensureOption(audioBitrateSelect, gpuProfile.audio?.bitrate ?? "192k");
      if (cpuProfileTierSelect) {
        ensureOption(cpuProfileTierSelect, cpuProfile.profile || gpuProfile.profile);
      }
      if (cpuProfileLevelSelect) {
        ensureOption(cpuProfileLevelSelect, cpuProfile.level || gpuProfile.level);
      }
      if (cpuProfileResolutionSelect) {
        ensureOption(
          cpuProfileResolutionSelect,
          cpuProfile.resolution || cpuProfile.max_resolution || gpuProfile.resolution,
        );
      }
      if (cpuProfileFpsSelect) {
        ensureOption(cpuProfileFpsSelect, cpuProfile.max_fps ?? gpuProfile.max_fps ?? 30);
      }
      if (cpuProfilePresetSelect) {
        ensureOption(cpuProfilePresetSelect, cpuProfile.preset || "slow");
      }
      if (cpuProfileRcSelect) {
        ensureOption(cpuProfileRcSelect, cpuProfile.rc || "crf");
      }
      if (cpuProfileCqSelect) {
        ensureOption(cpuProfileCqSelect, cpuProfile.cq ?? 20);
      }
      if (cpuProfileBitrateSelect) {
        ensureOption(
          cpuProfileBitrateSelect,
          cpuProfile.bitrate || cpuProfile.max_bitrate || gpuProfile.bitrate,
        );
      }
      if (cpuProfileMaxrateSelect) {
        ensureOption(cpuProfileMaxrateSelect, cpuProfile.max_bitrate || gpuProfile.max_bitrate);
      }
      if (cpuProfileBufsizeSelect) {
        ensureOption(cpuProfileBufsizeSelect, cpuProfile.bufsize || gpuProfile.bufsize);
      }
      if (cpuBframesSelect) {
        ensureOption(cpuBframesSelect, cpuProfile.bframes ?? gpuProfile.bframes ?? 2);
      }
      if (cpuLookaheadInput) {
        cpuLookaheadInput.value = cpuProfile.lookahead ?? 0;
      }
      if (cpuAdaptiveBframesSelect) {
        cpuAdaptiveBframesSelect.value = cpuProfile.adaptive_b_frames ? "1" : "0";
      }
      if (cpuAudioBitrateSelect) {
        ensureOption(
          cpuAudioBitrateSelect,
          cpuProfile.audio?.bitrate || gpuProfile.audio?.bitrate || "192k",
        );
      }
      if (gpuSummary) gpuSummary.textContent = summarizeProfile(gpuProfile, "GPU");
      if (cpuSummary) cpuSummary.textContent = summarizeProfile(cpuProfile, "CPU");

      syncLookaheadDisplay();
      enforceLevelConstraints();
      enforceCpuLevelConstraints();
      updateRateControlState();
      updateCpuRateControlState();
      updateBframeState();
      updateCpuBframeState();
      updateAqState();
      updateFfmpegPreview();
      updateCpuFfmpegPreview();
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
        const pipeline = job.pipeline || (job.encoding ? job.encoding.pipeline : null);
        const pipelineText = formatPipeline(pipeline);
        
        let elapsed = "-";
        if (status === "running") {
             elapsed = formatElapsed(jobElapsedSeconds(job));
        } else if (status !== "pending") {
             // /api/jobs does not include elapsed_seconds; fall back to the
             // started_at/updated_at delta instead of showing "<1s".
             const val = Number.isFinite(recordedElapsed) ? recordedElapsed : jobElapsedSeconds(job);
             elapsed = formatElapsed(val);
        }

        const truncatedId = job.id ? job.id.slice(0, 8) : "";
        tr.innerHTML = `
          <td>
            <button
              type="button"
              class="link-button job-link"
              data-job-id="${escapeHtml(truncatedId)}"
              data-job-id-full="${escapeHtml(job.id)}"
              data-job-path="${escapeHtml(normalizedPath)}"
            >
              ${escapeHtml(truncatedId)}
            </button>
          </td>
          <td class="status-${escapeHtml(status)}">${escapeHtml(statusLabel)}</td>
          <td>${escapeHtml(elapsed)}</td>
          <td class="path-cell" title="${escapeHtml(normalizedPath)}">${escapeHtml(fileName)}</td>
          <td>${escapeHtml(pipelineText)}</td>
          <td>${Number(progress) || 0}%</td>
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
      try {
        const response = await fetch("/api/jobs");
        if (response.ok) {
          const data = await response.json();
          jobCache = Array.isArray(data) ? data : [];
          renderJobTable();
        }
      } catch (e) {
        console.error("fetchJobs failed", e);
      }
    }

    async function fetchHistory() {
      try {
        const response = await fetch("/api/history?limit=50");
        if (response.ok) {
          const data = await response.json();
          historyCache = Array.isArray(data) ? data : [];
          renderHistoryTable();
        }
      } catch (e) {
        console.error("fetchHistory failed", e);
      }
    }

    function renderHistoryTable() {
      if (!historyRows) return;
      historyRows.innerHTML = "";
      historyCache.forEach((job) => {
        const tr = document.createElement("tr");
        const status = String(job.status || "unknown").toLowerCase();
        const statusLabel = status ? `${status[0].toUpperCase()}${status.slice(1)}` : "";
        const normalizedPath = normalizeDisplayPath(job.path);
        const fileName = fileNameFromPath(normalizedPath);
        const finished = job.updated_at ? new Date(job.updated_at).toLocaleString() : "-";
        const pipelineText = formatPipeline(job.pipeline || (job.encoding ? job.encoding.pipeline : null));
        // Inline onclick handlers cannot reach module-scoped functions; use a
        // data attribute and the delegated listener on the table body instead.
        const detailsLink = job.id
            ? `<button type="button" class="link-button history-log-link" data-job-id="${escapeHtml(job.id)}">View Logs</button>`
            : escapeHtml(job.message || "-");

        tr.innerHTML = `
          <td>${escapeHtml(job.id ? job.id.slice(0, 8) : "")}</td>
          <td class="status-${escapeHtml(status)}">${escapeHtml(statusLabel)}</td>
          <td>${escapeHtml(finished)}</td>
          <td class="path-cell" title="${escapeHtml(normalizedPath)}">${escapeHtml(fileName)}</td>
          <td>${escapeHtml(pipelineText)}</td>
          <td>${detailsLink}</td>
        `;
        historyRows.appendChild(tr);
      });
    }

    async function enqueueLibraryScan(library) {
      scanResult.textContent = library
        ? `Enqueuing ${library} scan...`
        : "Enqueuing scan for all libraries...";
      try {
        const response = await fetch("/api/scan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(library ? { library } : {}),
        });
        const result = await response.json();
        if (response.ok) {
          scanResult.textContent = `Scan scheduled for: ${(result.scheduled || []).join(", ")}`;
        } else {
          scanResult.textContent = `Scan failed: ${result.detail || "Unknown error"}`;
        }
      } catch (error) {
        scanResult.textContent = `Scan failed: ${error.message}`;
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
        const online = workerCount > 0;
        const statusColor = online ? (available > 0 ? "🟢" : "🟡") : "🔴";
        gpuMetrics.textContent = `GPU: ${statusColor} ${available}/${workerCount} ready`;
        gpuMetrics.className = "status-pill status-idle";
        if (!online) {
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

    let logRenderSignature = "";

    async function fetchLogs() {
      const params = new URLSearchParams();
      if (logSource.value) params.set("source", logSource.value);
      if (logCategory.value) params.set("category", logCategory.value);
      if (logLevel.value) params.set("min_severity", logLevel.value);
      if (logQuery.value) params.set("query", logQuery.value);
      try {
        const response = await fetch(`/api/logs?${params.toString()}`);
        if (response.ok) {
          const entries = await response.json();
          logCacheEntries = Array.isArray(entries) ? entries : [];
          // Skip re-render (and the scroll reset it causes) when nothing changed.
          const signature = JSON.stringify(logCacheEntries);
          if (signature === logRenderSignature) return;
          logRenderSignature = signature;
          const previousScrollTop = logList.scrollTop;
          logList.innerHTML = "";
          logCacheEntries.forEach((entry) => {
            const container = document.createElement("div");
            container.className = "log-entry";
            const severity = entry.severity || entry.level;
            container.innerHTML = `
              <div class="log-meta">
                <span class="log-pill ${severityClass(severity)}">${escapeHtml(severity)}</span>
                <span class="log-pill pill-muted">${escapeHtml(entry.source || entry.logger)}</span>
                <span class="log-pill pill-outline">${escapeHtml(entry.category || entry.logger)}</span>
                <span class="log-timestamp log-pill pill-outline">${escapeHtml(formatLogTimestamp(entry.timestamp))}</span>
              </div>
              <div class="log-message">${escapeHtml(entry.message)}</div>
            `;
            logList.appendChild(container);
          });
          logList.scrollTop = previousScrollTop;
        }
      } catch (e) {
        console.error("fetchLogs failed", e);
      }
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
      const existing = libraryEntries.find((item) => item.id === update.id);
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
      const normalized = escapeHtml(status || "unknown");
      return `<span class="status-chip status-${normalized}">${normalized}</span>`;
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
        const encodingLabel = formatPipeline({
          decode_type: entry.decode_type,
          scale_type: entry.scale_type,
          encode_type: entry.encode_type,
          attempt: entry.attempt,
          max_attempts: entry.max_attempts,
        });

        let statusHtml = formatStatusChip(entry.status);
        if (entry.status === "converting" && entry.last_job_id) {
            // Keep status as "Converting" without percentage
            // The percentage info is redundant here as per user request.
        }

        let errorHtml = "";
        if (entry.status === "failed" && entry.last_error) {
            errorHtml = `<div class="warn" style="margin-top:0.25rem; font-size:0.85rem;">${escapeHtml(entry.last_error)}</div>`;
        }

        tr.innerHTML = `
          <td>${Number(entry.id)}</td>
          <td>${statusHtml}</td>
          <td>${escapeHtml(entry.library)}</td>
          <td class="path-cell" title="${escapeHtml(normalizedPath)}">
            ${escapeHtml(fileName)}
            ${errorHtml}
          </td>
          <td>${escapeHtml(encodingLabel)}</td>
          <td>${escapeHtml(formatUpdated(entry.updated_at))}</td>
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

    // Background refresh that re-fetches the already-loaded window instead of
    // resetting pagination — "Load more" progress and scroll survive.
    async function softRefreshLibraryEntries() {
      if (entryLoadingState) return;
      const pageSize = Number(entryPageSize.value || "20");
      const windowSize = Math.max(libraryEntries.length, pageSize);
      entryLoadingState = true;
      try {
        const params = new URLSearchParams({
          limit: String(windowSize),
          offset: "0",
          include_total: "true",
        });
        if (entryStatusFilter.value) params.set("status", entryStatusFilter.value);
        if (entryLibraryFilter.value) params.set("library", entryLibraryFilter.value);
        const response = await fetch(`/api/library/entries?${params.toString()}`);
        const result = await response.json();
        const items = Array.isArray(result) ? result : result.items || [];
        if (!Array.isArray(result) && result.total !== undefined && result.total !== null) {
          entryTotal = result.total;
        }
        libraryEntries = [...items].sort(
          (a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0),
        );
        entryOffset = items.length;
        entryHasMore =
          entryTotal !== null ? entryOffset < entryTotal : items.length === windowSize;
        renderEntrySummary();
        renderEntryTable();
      } catch (error) {
        console.error("softRefreshLibraryEntries failed", error);
      } finally {
        entryLoadingState = false;
      }
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
      if (logRetention) { // Check if element exists due to refactor
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
            softRefreshLibraryEntries();
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

    if (scanButtons) {
      scanButtons.addEventListener("click", (event) => {
        const button = event.target.closest("button");
        if (!button) return;
        if (button.id === "scan-all") {
          enqueueLibraryScan(null);
        } else if (button.dataset.scanLibrary) {
          enqueueLibraryScan(button.dataset.scanLibrary);
        }
      });
    }

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

    // Removed event listener for #profile-select

    profileRcSelect.addEventListener("change", () => {
      updateRateControlState();
      updateFfmpegPreview();
    });

    if (cpuProfileRcSelect) {
      cpuProfileRcSelect.addEventListener("change", () => {
        updateCpuRateControlState();
      });
    }

    profileResolutionSelect.addEventListener("change", () => {
      enforceLevelConstraints("resolution");
      updateFfmpegPreview();
    });

    if (cpuProfileResolutionSelect) {
      cpuProfileResolutionSelect.addEventListener("change", () => {
        enforceCpuLevelConstraints("resolution");
      });
    }

    profileFpsSelect.addEventListener("change", () => {
      enforceLevelConstraints("fps");
      updateFfmpegPreview();
    });

    if (cpuProfileFpsSelect) {
      cpuProfileFpsSelect.addEventListener("change", () => {
        enforceCpuLevelConstraints("fps");
      });
    }

    if (cpuProfileLevelSelect) {
      cpuProfileLevelSelect.addEventListener("change", () => {
        enforceCpuLevelConstraints("level");
      });
    }

    profileTierSelect.addEventListener("change", () => {
      updateBframeState();
      updateFfmpegPreview();
    });

    if (cpuProfileTierSelect) {
      cpuProfileTierSelect.addEventListener("change", () => {
        updateCpuBframeState();
      });
    }

    bframesSelect.addEventListener("change", () => {
      updateBframeState();
      updateFfmpegPreview();
    });

    if (cpuBframesSelect) {
      cpuBframesSelect.addEventListener("change", () => {
        updateCpuBframeState();
      });
    }

    adaptiveBframesSelect.addEventListener("change", updateFfmpegPreview);

    lookaheadInput.addEventListener("input", () => {
      syncLookaheadDisplay();
      updateBframeState();
      updateFfmpegPreview();
    });

    if (cpuLookaheadInput) {
      cpuLookaheadInput.addEventListener("input", () => {
        syncLookaheadDisplay();
        updateCpuBframeState();
      });
    }

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

    if (setupWizard) {
      wizardSetupBtn.addEventListener("click", () => {
        setupWizard.close();
        switchPage("config");
        document.querySelector("#libraries-panel")?.scrollIntoView({ behavior: "smooth" });
        libraryNameInput?.focus();
      });
      wizardSkipBtn.addEventListener("click", () => {
        setupWizard.close();
        sessionStorage.setItem("wizardSkipped", "true");
      });
    }

    if (refreshHistoryBtn) {
        refreshHistoryBtn.addEventListener("click", fetchHistory);
    }

    if (resetConfigBtn) {
        resetConfigBtn.addEventListener("click", async () => {
            if (!confirm("Are you sure you want to reset configuration to defaults? This cannot be undone.")) return;
            resetConfigBtn.disabled = true;
            resetConfigBtn.textContent = "Resetting...";
            try {
                const response = await fetch("/api/config/reset", { method: "POST" });
                if (response.ok) {
                    alert("Configuration reset to defaults.");
                    fetchConfig();
                } else {
                    alert("Failed to reset configuration.");
                }
            } catch (e) {
                alert("Error resetting configuration.");
            } finally {
                resetConfigBtn.disabled = false;
                resetConfigBtn.textContent = "Reset to Defaults";
            }
        });
    }

    if (purgeJobsBtn) {
        purgeJobsBtn.addEventListener("click", async () => {
            if (!confirm("Remove all queued jobs that are not currently running?")) return;
            purgeJobsBtn.disabled = true;
            purgeJobsResult.textContent = "Purging inactive jobs...";
            try {
                const response = await fetch("/api/jobs/purge-inactive", { method: "POST" });
                const result = await response.json();
                if (response.ok) {
                    purgeJobsResult.textContent =
                        `Removed ${result.removed_jobs} jobs (acked ${result.pending_messages_acknowledged} pending, trimmed ${result.stream_entries_deleted} stream entries).`;
                    await Promise.all([fetchJobs(), refreshQueueState()]);
                } else {
                    purgeJobsResult.textContent = `Purge failed: ${result.detail || "Unknown error"}`;
                }
            } catch (error) {
                console.error("Purge failed", error);
                purgeJobsResult.textContent = "Purge failed: see console for details.";
            } finally {
                purgeJobsBtn.disabled = false;
            }
        });
    }

    const discardConfigBtn = document.querySelector("#discard-config");
    if (discardConfigBtn) {
      discardConfigBtn.addEventListener("click", () => {
        const profile = findProfileById(selectedProfileId) || profileList()[0] || null;
        if (profile) {
          creatingProfile = false;
          selectedProfileId = profile.id ?? null;
          loadProfile(profile);
          if (profileNameInput) profileNameInput.value = profile.name || "";
          renderProfileSelect();
        }
        configResult.textContent = "Changes discarded.";
        setTimeout(() => { configResult.textContent = ""; }, 2000);
      });
    }

    if (profileSelect) {
      profileSelect.addEventListener("change", () => {
        const profile = findProfileById(profileSelect.value);
        if (!profile) return;
        creatingProfile = false;
        selectedProfileId = profile.id;
        loadProfile(profile);
        if (profileNameInput) profileNameInput.value = profile.name || "";
        if (profileToolbarResult) profileToolbarResult.textContent = "";
      });
    }

    if (newProfileBtn) {
      newProfileBtn.addEventListener("click", () => {
        creatingProfile = true;
        selectedProfileId = null;
        if (profileSelect) profileSelect.selectedIndex = -1;
        if (profileNameInput) {
          profileNameInput.value = "";
          profileNameInput.focus();
        }
        if (profileToolbarResult) {
          profileToolbarResult.textContent =
            "Creating a new profile from the current values — name it and save.";
        }
      });
    }

    if (deleteProfileBtn) {
      deleteProfileBtn.addEventListener("click", async () => {
        const profile = findProfileById(selectedProfileId);
        if (!profile) {
          if (profileToolbarResult) profileToolbarResult.textContent = "No saved profile selected.";
          return;
        }
        if (!confirm(`Delete profile "${profile.name}"?`)) return;
        deleteProfileBtn.disabled = true;
        try {
          const response = await fetch(`/api/profiles/${profile.id}`, { method: "DELETE" });
          if (response.ok) {
            selectedProfileId = null;
            if (profileToolbarResult) {
              profileToolbarResult.textContent = `Deleted profile ${profile.name}.`;
            }
            await fetchConfig();
          } else {
            const detail = await response.json().catch(() => ({}));
            if (profileToolbarResult) {
              profileToolbarResult.textContent =
                detail.detail || "Delete failed (profile may be in use by a library).";
            }
          }
        } finally {
          deleteProfileBtn.disabled = false;
        }
      });
    }

    if (libraryForm) {
      libraryForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const name = (libraryNameInput?.value || "").trim();
        const root = (libraryRootInput?.value || "").trim();
        const profileId = Number(libraryProfileSelect?.value || "0");
        if (!name || !root || !profileId) {
          libraryFormResult.textContent = "Name, root path, and profile are required.";
          return;
        }
        libraryFormResult.textContent = "Adding library...";
        try {
          const response = await fetch("/api/libraries", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, root, profile_id: profileId }),
          });
          const result = await response.json().catch(() => ({}));
          if (response.ok) {
            libraryFormResult.textContent = `Library ${name} added; initial scan scheduled.`;
            libraryNameInput.value = "";
            libraryRootInput.value = "";
            await fetchConfig();
            refreshLibraryEntries();
          } else {
            libraryFormResult.textContent = result.detail || "Failed to add library.";
          }
        } catch (error) {
          libraryFormResult.textContent = `Failed to add library: ${error.message}`;
        }
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

    [
      cpuProfileRcSelect,
      cpuProfileCqSelect,
      cpuProfileBitrateSelect,
      cpuProfileMaxrateSelect,
      cpuProfileBufsizeSelect,
      cpuProfilePresetSelect,
      cpuProfileTierSelect,
      cpuProfileLevelSelect,
      cpuProfileResolutionSelect,
      cpuProfileFpsSelect,
      cpuBframesSelect,
      cpuAdaptiveBframesSelect,
      cpuAudioBitrateSelect,
    ].forEach((element) => {
        if (!element) return;
        element.addEventListener("change", (event) => {
            if (event.target === cpuProfileLevelSelect) {
                enforceCpuLevelConstraints("level");
            } else if (event.target === cpuProfileResolutionSelect) {
                enforceCpuLevelConstraints("resolution");
            } else if (event.target === cpuProfileFpsSelect) {
                enforceCpuLevelConstraints("fps");
            } else if (event.target === cpuBframesSelect) {
                updateCpuBframeState();
            } else if (event.target === cpuProfileRcSelect) {
                updateCpuRateControlState();
            }
            updateCpuFfmpegPreview();
        });
    });

    if (cpuLookaheadInput) {
        cpuLookaheadInput.addEventListener("input", () => {
            syncLookaheadDisplay();
            updateCpuBframeState();
            updateCpuFfmpegPreview();
        });
    }

    if (reprocessAllBtn) {
        reprocessAllBtn.addEventListener("click", async () => {
            if (!confirm("Are you sure you want to reprocess ALL entries? This will delete existing converted files and queue them for conversion. Original files are required.")) return;
            reprocessAllBtn.disabled = true;
            reprocessAllBtn.textContent = "Processing...";
            try {
                const response = await fetch("/api/library/entries/reprocess-all", { method: "POST" });
                const result = await response.json();
                if (response.ok) {
                    alert(`Queued reprocessing for ${result.queued_count} entries.`);
                    refreshLibraryEntries();
                } else {
                    alert("Failed to reprocess: " + (result.detail || "Unknown error"));
                }
            } catch (e) {
                alert("Error during reprocess request.");
            } finally {
                reprocessAllBtn.disabled = false;
                reprocessAllBtn.textContent = "Reprocess All";
            }
        });
    }

    if (deleteAllOriginalsBtn) {
        deleteAllOriginalsBtn.addEventListener("click", async () => {
            if (!confirm("Are you sure you want to DELETE ALL ORIGINAL files where a successful conversion exists? This CANNOT be undone.")) return;
            deleteAllOriginalsBtn.disabled = true;
            deleteAllOriginalsBtn.textContent = "Processing...";
            try {
                const response = await fetch("/api/library/entries/delete-all-originals", { method: "POST" });
                const result = await response.json();
                if (response.ok) {
                    alert(`Queued deletion for ${result.queued_count} original files.`);
                    refreshLibraryEntries();
                } else {
                    alert("Failed to delete originals: " + (result.detail || "Unknown error"));
                }
            } catch (e) {
                alert("Error during delete request.");
            } finally {
                deleteAllOriginalsBtn.disabled = false;
                deleteAllOriginalsBtn.textContent = "Delete All Originals";
            }
        });
    }

    // Consolidated ops-config-form handler
    document.querySelector("#ops-config-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const interval = Number(opsScanInterval.value || "0");
      const retentionDays = Number(logRetention.value || "7"); // Get log retention from its new location

      opsConfigResult.textContent = "Saving operational settings...";
      logConfigResult.textContent = "Saving logging settings..."; // Show separate saving messages

      let operationalSuccess = false;
      let loggingSuccess = false;
      let operationalMessage = "";
      let loggingMessage = "";

      // API call for operational settings
      try {
        const response = await fetch("/api/config/operational", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scan_interval_min: interval }),
        });
        const result = await response.json();
        if (response.ok) {
          operationalSuccess = true;
          operationalMessage = `Scan interval updated to ${result.scan_interval_min} minutes.`;
          if (configCache) {
            configCache.operational = configCache.operational || {};
            configCache.operational.scan_interval_min = result.scan_interval_min;
            if (result.revision) {
              configCache.revision = result.revision;
            }
          }
        } else {
          operationalMessage = `Operational settings save failed: ${result.detail || "Unknown error"}`;
        }
      } catch (error) {
        operationalMessage = `Operational settings save failed: ${error.message}`;
      } finally {
        opsConfigResult.textContent = operationalMessage;
      }

      // API call for logging settings
      try {
        const response = await fetch("/api/config/logging", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ retention_days: retentionDays }),
        });
        const result = await response.json();
        if (response.ok) {
          loggingSuccess = true;
          loggingMessage = `Retention updated to ${result.retention_days} days.`;
          if (configCache) {
            configCache.logging = configCache.logging || {};
            configCache.logging.retention_days = result.retention_days;
            if (result.revision) {
              configCache.revision = result.revision;
            }
          }
          fetchLogStats(); // Refresh log stats after saving
          fetchLogs(); // Refresh logs after saving
        } else {
          loggingMessage = `Logging settings save failed: ${result.detail || "Unknown error"}`;
        }
      } catch (error) {
        loggingMessage = `Logging settings save failed: ${error.message}`;
      } finally {
        logConfigResult.textContent = loggingMessage;
      }

      // If both succeeded, clear messages after a delay
      if (operationalSuccess && loggingSuccess) {
        setTimeout(() => {
          opsConfigResult.textContent = "";
          logConfigResult.textContent = "";
        }, 3000);
      }
    });

    document.querySelector("#config-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const profileName = (profileNameInput?.value || "").trim();
      if (!profileName) {
        configResult.textContent = "Profile name is required.";
        profileNameInput?.focus();
        return;
      }

      const gpuPayload = {
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
      const existingProfile = findProfileById(selectedProfileId) || {};
      const cpuSource = existingProfile.cpu || {};
      const cpuLookaheadValue = Math.max(
        0,
        Number(cpuLookaheadInput?.value ?? cpuSource.lookahead ?? 0),
      );
      const cpuBframesValue = Number(
        cpuBframesSelect?.value ?? cpuSource.bframes ?? gpuPayload.bframes ?? 2,
      );
      const cpuAdaptiveAllowed = cpuLookaheadValue > 0 && cpuBframesValue > 0;
      const cpuAdaptiveSelected = cpuAdaptiveBframesSelect?.value === "1";
      const cpuPayload = {
        codec: cpuSource.codec || "h264",
        profile: cpuProfileTierSelect?.value || cpuSource.profile || gpuPayload.profile,
        level: cpuProfileLevelSelect?.value || cpuSource.level || gpuPayload.level,
        resolution:
          cpuProfileResolutionSelect?.value ||
          cpuSource.resolution ||
          cpuSource.max_resolution ||
          gpuPayload.resolution,
        max_fps: Number(
          cpuProfileFpsSelect?.value || cpuSource.max_fps || gpuPayload.max_fps || 30,
        ),
        bitrate:
          cpuProfileBitrateSelect?.value ||
          cpuSource.bitrate ||
          cpuSource.max_bitrate ||
          gpuPayload.bitrate,
        max_bitrate:
          cpuProfileMaxrateSelect?.value || cpuSource.max_bitrate || gpuPayload.max_bitrate,
        bufsize: cpuProfileBufsizeSelect?.value || cpuSource.bufsize || gpuPayload.bufsize,
        preset: cpuProfilePresetSelect?.value || cpuSource.preset || "slow",
        rc: cpuProfileRcSelect?.value || cpuSource.rc || "crf",
        cq: Number(cpuProfileCqSelect?.value ?? cpuSource.cq ?? 20),
        bframes: cpuBframesValue,
        lookahead: cpuLookaheadValue,
        adaptive_b_frames: cpuAdaptiveAllowed && cpuAdaptiveSelected,
        aq: cpuSource.aq !== false,
        spatial_aq: cpuSource.spatial_aq ?? false,
        temporal_aq: cpuSource.temporal_aq ?? false,
        aq_strength: Number(cpuSource.aq_strength ?? 7),
        audio: {
          codec: cpuSource.audio?.codec || gpuPayload.audio?.codec || "aac",
          bitrate:
            cpuAudioBitrateSelect?.value ||
            cpuSource.audio?.bitrate ||
            gpuPayload.audio?.bitrate ||
            "192k",
          channels: 2,
        },
      };
      const payload = { name: profileName, gpu: gpuPayload, cpu: cpuPayload };
      // Update the selected profile in place (PUT keeps the id that libraries
      // reference); POST only when explicitly creating a new profile.
      const isUpdate = !creatingProfile && selectedProfileId !== null;
      const endpoint = isUpdate ? `/api/profiles/${selectedProfileId}` : "/api/profiles";
      configResult.textContent = "Saving...";
      try {
        const response = await fetch(endpoint, {
          method: isUpdate ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const result = await response.json().catch(() => ({}));
        if (response.ok) {
          creatingProfile = false;
          selectedProfileId = result.profile?.id ?? selectedProfileId;
          configResult.textContent = `${isUpdate ? "Updated" : "Created"} profile ${
            result.profile?.name || profileName
          }`;
          if (profileToolbarResult) profileToolbarResult.textContent = "";
          await fetchConfig();
        } else {
          configResult.textContent = `Save failed: ${result.detail || response.statusText}`;
        }
      } catch (error) {
        configResult.textContent = `Save failed: ${error.message}`;
      }
    });

    refreshEntriesBtn.addEventListener("click", refreshLibraryEntries);

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
      logQuery.value = jobId || jobPath;
      switchPage("logs");
      fetchLogs();
    });

    if (historyRows) {
      historyRows.addEventListener("click", (event) => {
        const link = event.target.closest(".history-log-link");
        if (!link) return;
        logQuery.value = link.dataset.jobId || "";
        switchPage("logs");
        fetchLogs();
      });
    }

    async function copyLogsToClipboard() {
      if (!copyLogsButton) return;
      const entries = logCacheEntries || [];
      if (!entries.length) {
        copyLogsButton.textContent = "No logs";
        setTimeout(() => {
          copyLogsButton.textContent = "Copy all";
        }, 1500);
        return;
      }
      const lines = entries.map((entry) => {
        const severity = entry.severity || entry.level || "INFO";
        const timestamp = entry.timestamp || "";
        const source = entry.source || entry.logger || "app";
        const category = entry.category || "-";
        const message = entry.message || "";
        return `[${timestamp}] ${severity} ${source}/${category} ${message}`;
      });
      const text = lines.join("\n");
      try {
        await navigator.clipboard.writeText(text);
        copyLogsButton.textContent = "Copied!";
      } catch (error) {
        console.error("Clipboard copy failed", error);
        copyLogsButton.textContent = "Copy failed";
      } finally {
        setTimeout(() => {
          copyLogsButton.textContent = "Copy all";
        }, 2000);
      }
    }

    if (copyLogsButton && navigator?.clipboard) {
      copyLogsButton.addEventListener("click", copyLogsToClipboard);
    } else if (copyLogsButton) {
      copyLogsButton.disabled = true;
      copyLogsButton.title = "Clipboard API unavailable";
    }

    // Check URL params for direct log linking
    const urlParams = new URLSearchParams(window.location.search);
    const targetJobId = urlParams.get("job_id");
    if (targetJobId) {
      logQuery.value = targetJobId;
      // Clear the param so refreshing doesn't get stuck
      window.history.replaceState({}, "", window.location.pathname + window.location.hash);
      switchPage("logs");
      // fetchLogs will be called by the interval or initial load
    }

    fetchConfig();
    fetchJobs();
    fetchHistory();
    fetchLogFilters();
    fetchLogs();
    fetchLogStats();
    refreshLibraryEntries();
    refreshQueueState();
    connectLiveUpdates();
    const initialPage = window.location.hash.replace("#", "") || "queue";
    
    // Search Persistence
    if (sessionStorage.getItem("logQuery")) {
        logQuery.value = sessionStorage.getItem("logQuery");
    }
    if (sessionStorage.getItem("entrySearch")) {
        entrySearch.value = sessionStorage.getItem("entrySearch");
    }

    logQuery.addEventListener("input", () => {
        sessionStorage.setItem("logQuery", logQuery.value);
    });
    entrySearch.addEventListener("input", () => {
        sessionStorage.setItem("entrySearch", entrySearch.value);
        renderEntryTable();
    });

    switchPage(initialPage);

    // Poll only what the visible page needs, and nothing while the tab is
    // hidden; the WebSocket carries live job/entry updates in between.
    setInterval(pollWhen(["queue"], fetchJobs), 5000);
    setInterval(pollWhen(["queue"], refreshQueueState), 7000);
    setInterval(pollWhen(["history"], fetchHistory), 15000);
    setInterval(pollWhen(["logs"], fetchLogs), 6000);
    setInterval(pollWhen(["logs"], fetchLogFilters), 30000);
    setInterval(pollWhen(["config"], fetchLogStats), 30000);
    setInterval(pollWhen(["library"], softRefreshLibraryEntries), 30000);

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        refreshPageData(activePageName());
      }
    });
