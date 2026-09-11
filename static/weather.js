async function syncWeatherData() {
    const syncBtn = document.getElementById("sync-weather-btn");
    const cityInput = document.getElementById("city-input");
    const weatherStatus = document.getElementById("weather-status");

    const city = cityInput ? cityInput.value : "Delhi";

    // 1. Disable button & show loading state
    if (syncBtn) {
        syncBtn.disabled = true;
        syncBtn.innerText = "Syncing...";
    }
    if (weatherStatus) weatherStatus.innerText = "Fetching live weather...";

    try {
        // 2. Call the Flask weather endpoint
        const response = await fetch(`/api/weather?city=${encodeURIComponent(city)}`);
        const data = await response.json();

        // 3. Render updated weather data
        if (data.success) {
            const tempDisplay = document.getElementById("temp-display");
            const condDisplay = document.getElementById("condition-display");
            
            if (tempDisplay) tempDisplay.innerText = `${data.temp} °C`;
            if (condDisplay) condDisplay.innerText = data.condition;
            if (weatherStatus) weatherStatus.innerText = `Updated for ${data.city}`;
        } else {
            if (weatherStatus) weatherStatus.innerText = `Error: ${data.error || "Failed to fetch weather"}`;
        }
    } catch (err) {
        if (weatherStatus) weatherStatus.innerText = "Failed to connect to weather server.";
    } finally {
        // 4. Re-enable button
        if (syncBtn) {
            syncBtn.disabled = false;
            syncBtn.innerText = "Sync Weather";
        }
    }
}

// Bind event listener to the sync button when DOM is ready
document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("sync-weather-btn");
    if (btn) {
        btn.addEventListener("click", syncWeatherData);
    }
});
