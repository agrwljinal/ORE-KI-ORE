(function () {
  "use strict";

  var AUTO_SYNC_MS = 5 * 60 * 1000;
  var DEFAULT_CITY = "Balaghat";
  var autoSyncTimer = null;
  var rainLocked = false;
  var lastRainfallValue = 88.5;

  function $(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    var el = $(id);
    if (el) el.textContent = value == null ? "--" : String(value);
  }

  function severityColour(severityPct) {
    if (severityPct >= 60) return "#f87171";
    if (severityPct >= 30) return "#fbbf24";
    return "#60a5fa";
  }

  function renderWeatherPanel(data) {
    var live = data.live || {};
    var scenario = data.weather_scenario || {};
    var impact = data.weather_impact || null;

    if (live.success) {
      setText("temp-display", live.temp != null ? Number(live.temp).toFixed(1) : "--");
      setText("condition-display", live.condition || "n/a");
      var site = data.site || {};
      var at = (site.label || (live.city || data.city));
      setText("weather-status", "Live @ " + at);
    } else {
      setText("temp-display", "--");
      setText("condition-display", "no feed");
      var statusMsg = data.live_status === "missing_key"
        ? "Live weather needs OPENWEATHER_API_KEY"
        : (data.live_error || "Live weather unavailable");
      setText("weather-status", statusMsg);
    }

    var severityPct = scenario.severity_pct != null ? Number(scenario.severity_pct) : 0;
    setText("weather-severity", Math.round(severityPct) + "%");
    var bar = $("weather-severity-bar");
    if (bar) {
      bar.style.width = Math.min(100, Math.max(0, severityPct)) + "%";
      bar.style.background = severityColour(severityPct);
    }

    if (impact && impact.impact_tonnes != null) {
      setText("weather-impact", "≈ " + Number(impact.impact_tonnes).toLocaleString(undefined, { maximumFractionDigits: 1 }) + " t vs clear day");
      var note = impact.weather_forecast_tonnes != null
        ? "Model forecast this weather ≈ " + Number(impact.weather_forecast_tonnes).toLocaleString(undefined, { maximumFractionDigits: 0 }) + " t (clear ≈ " + Number(impact.clear_weather_forecast_tonnes).toLocaleString(undefined, { maximumFractionDigits: 0 }) + " t)"
        : "";
      setText("weather-impact-note", note);
    } else {
      setText("weather-impact", "— t");
      setText("weather-impact-note", impact ? impact.note : "");
    }

    setText("rainfall-display", live.rainfall_mm_24h != null ? Number(live.rainfall_mm_24h).toFixed(1) + " mm" : "--");
    setText("rain-level-display", live.rain_level || "n/a");
  }

  function currentState() {
    function slider(id, fallback) {
      var el = $(id);
      return el ? parseFloat(el.value) : fallback;
    }
    return {
      rainfall_mm: slider("slider-rainfall", 88.5),
      soil_moisture_pct: slider("slider-soil-moisture", 38),
      equipment_downtime_hours: slider("slider-downtime", 6),
      blast_delay_minutes: slider("slider-blast-delay", 45)
    };
  }

  function refreshWeatherPanel(cityOverride) {
    var city = (cityOverride || getCity()).trim() || DEFAULT_CITY;
    fetch("/api/weather?city=" + encodeURIComponent(city), { headers: { "cache-control": "no-cache" } })
      .then(function (res) { return res.json(); })
      .then(renderWeatherPanel)
      .catch(function () {
        setText("weather-status", "Failed to reach weather service.");
      });
  }

  function getCity() {
    var el = $("city-input");
    return el ? (el.value || DEFAULT_CITY) : DEFAULT_CITY;
  }

  function applyEffectiveInputs(inputs) {
    if (!inputs) return;
    var mapping = {
      "slider-rainfall": inputs.rainfall_mm,
      "slider-soil-moisture": inputs.soil_moisture_pct,
      "slider-downtime": inputs.equipment_downtime_hours,
      "slider-blast-delay": inputs.blast_delay_minutes
    };
    Object.keys(mapping).forEach(function (id) {
      var el = $(id);
      if (!el || mapping[id] == null) return;
      var min = parseFloat(el.min), max = parseFloat(el.max), step = parseFloat(el.step) || 1;
      var value = Math.min(max, Math.max(min, Number(mapping[id])));
      value = Math.round(value / step) * step;
      el.value = Math.min(max, Math.max(min, value));
    });
    if (typeof window.updateControlBadges === "function" && typeof window.getControls === "function") {
      try { window.updateControlBadges(window.getControls()); } catch (e) { /* ignore */ }
    }
  }

  function syncWeatherNow() {
    var button = $("sync-weather-btn");
    var originalText = button ? button.textContent : "";
    if (button) { button.disabled = true; button.textContent = "Syncing..."; }
    setText("weather-status", "Fetching live weather...");

    fetch("/api/weather", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ city: getCity() })
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.status !== "success" || data.live_status !== "ok" || !data.live || !data.live.success) {
          renderWeatherPanel(data);
          setText("weather-status", data.message || data.live_error || "Live weather unavailable; nothing applied.");
          return;
        }
        var inputs = data.weather_scenario && data.weather_scenario.effective_inputs;
        if (inputs) {
          // Rainfall slider is API-driven: use the live 24h total from the API,
          // not the weather-translated value.
          inputs.rainfall_mm = (data.live && data.live.rainfall_mm_24h != null)
            ? data.live.rainfall_mm_24h
            : inputs.rainfall_mm;
          inputs.rainfall_mm = Math.min(250, Math.max(0, Number(inputs.rainfall_mm) || 0));
        }
        applyEffectiveInputs(inputs);
        renderWeatherPanel(data);
        if (typeof window.refreshAll === "function") {
          window.refreshAll(true);
        }
      })
      .catch(function () {
        setText("weather-status", "Failed to connect to weather service.");
      })
      .then(function () {
        if (button) { button.disabled = false; button.textContent = originalText || "Sync Weather"; }
      });
  }

  function startAutoSync() {
    stopAutoSync();
    autoSyncTimer = setInterval(syncWeatherNow, AUTO_SYNC_MS);
  }

  function stopAutoSync() {
    if (autoSyncTimer) {
      clearInterval(autoSyncTimer);
      autoSyncTimer = null;
    }
  }

  function bind() {
    var button = $("sync-weather-btn");
    if (button) button.addEventListener("click", syncWeatherNow);

    // Rainfall is API-driven while auto-sync is ON; free for manual entry
  // when auto-sync is OFF.
  function setRainfallLocked(locked) {
    var el = $("slider-rainfall");
    if (!el) return;
    rainLocked = !!locked;
    if (locked && el.value != null && el.value !== "") {
      lastRainfallValue = parseFloat(el.value) || lastRainfallValue;
    }
    el.disabled = locked;
    el.style.cursor = locked ? "not-allowed" : "";
    el.style.opacity = locked ? "0.6" : "";
    el.title = locked
      ? "Locked to the live API while auto-sync is on."
      : "Manual entry (auto-sync off).";
  }

    var toggle = $("toggle-weather-sync");
    if (toggle) {
      toggle.addEventListener("change", function () {
        setRainfallLocked(toggle.checked);
        if (toggle.checked) startAutoSync(); else stopAutoSync();
      });
      setRainfallLocked(toggle.checked);
      if (toggle.checked) startAutoSync();
    }

    // Defence-in-depth: while auto-sync is ON, reject any manual slider
    // movement even if something else transiently enabled the element.
    var guardSlider = $("slider-rainfall");
    if (guardSlider) {
      function guardRain(ev) {
        if (!rainLocked) return;
        if (guardSlider.disabled) { ev.preventDefault(); return; }
        guardSlider.value = lastRainfallValue;
        guardSlider.disabled = true;
        ev.preventDefault();
        if (typeof window.updateControlBadges === "function" && typeof window.getControls === "function") {
          try { window.updateControlBadges(window.getControls()); } catch (e) { /* ignore */ }
        }
      }
      guardSlider.addEventListener("input", guardRain);
      guardSlider.addEventListener("change", guardRain);
    }

    var debounce = null;
    ["slider-rainfall", "slider-soil-moisture", "slider-downtime", "slider-blast-delay", "input-target"].forEach(function (id) {
      var el = $(id);
      if (!el) return;
      el.addEventListener("change", function () {
        if (debounce) clearTimeout(debounce);
        debounce = setTimeout(function () { refreshWeatherPanel(); }, 350);
      });
    });

    refreshWeatherPanel();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();