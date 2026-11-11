(() => {
    const svgNamespace = "http://www.w3.org/2000/svg";
    const status = document.getElementById("connectionStatus");
    const statusText = document.getElementById("connectionStatusText");
    let initialTotal = null;
    let inFlight = false;

    function svgElement(name, attributes = {}) {
        const element = document.createElementNS(svgNamespace, name);
        for (const [attribute, value] of Object.entries(attributes)) {
            element.setAttribute(attribute, String(value));
        }
        return element;
    }

    function drawTrafficChart(buckets) {
        const left = 64;
        const top = 24;
        const width = 720;
        const height = 220;
        const peak = Math.max(...buckets, 1);
        const step = Math.max(1, Math.ceil(peak / 4));
        const maximum = step * 4;
        const x = (index) => left + (index / Math.max(1, buckets.length - 1)) * width;
        const y = (count) => top + height - (count / maximum) * height;
        const grid = document.getElementById("liveTrafficGrid");
        const markers = document.getElementById("liveTrafficPoints");
        const coordinates = [];
        grid.replaceChildren();
        markers.replaceChildren();

        for (let tick = 0; tick <= 4; tick += 1) {
            const value = step * tick;
            const yPosition = y(value);
            grid.append(
                svgElement("line", {
                    x1: left,
                    x2: left + width,
                    y1: yPosition,
                    y2: yPosition,
                    class: "chart-grid-line",
                }),
            );
            const label = svgElement("text", {
                x: left - 10,
                y: yPosition + 4,
                class: "chart-tick",
                "text-anchor": "end",
            });
            label.textContent = value.toLocaleString();
            grid.append(label);
        }

        for (const [index, label] of [
            [0, "60 sec ago"],
            [Math.floor((buckets.length - 1) / 2), "30 sec ago"],
            [buckets.length - 1, "Now"],
        ]) {
            const tick = svgElement("text", {
                x: x(index),
                y: top + height + 22,
                class: "chart-tick",
                "text-anchor": index === 0 ? "start" : index === buckets.length - 1 ? "end" : "middle",
            });
            tick.textContent = label;
            grid.append(tick);
        }

        buckets.forEach((count, index) => {
            const xPosition = x(index);
            const yPosition = y(count);
            coordinates.push(`${xPosition},${yPosition}`);
            const marker = svgElement("circle", {
                cx: xPosition,
                cy: yPosition,
                r: 3,
                class: "cache-point",
            });
            const tooltip = svgElement("title");
            tooltip.textContent = `${count} request${count === 1 ? "" : "s"} in interval ${index + 1}`;
            marker.append(tooltip);
            markers.append(marker);
        });

        document.getElementById("liveTrafficLine").setAttribute("points", coordinates.join(" "));
        document.getElementById("liveTrafficChart").setAttribute(
            "aria-label",
            `Incoming application requests in the last minute, grouped into five-second intervals. Peak interval: ${peak} requests.`,
        );
    }

    async function refreshTraffic() {
        if (inFlight || document.hidden) {
            return;
        }
        inFlight = true;
        try {
            const response = await fetch("/api/cache/traffic", { cache: "no-store" });
            const data = await response.json();
            if (!response.ok || !Array.isArray(data.buckets)) {
                throw new Error("Traffic metrics are unavailable.");
            }
            if (initialTotal === null) {
                initialTotal = data.total_requests;
            }
            document.getElementById("monitorRequests").textContent =
                Math.max(0, data.total_requests - initialTotal).toLocaleString();
            document.getElementById("totalRequests").textContent =
                data.total_requests.toLocaleString();
            document.getElementById("recentRequests").textContent =
                data.recent_requests.toLocaleString();
            drawTrafficChart(data.buckets);
            statusText.textContent = "Live · updated just now";
            status.classList.remove("offline");
        } catch {
            statusText.textContent = "Connection lost · retrying";
            status.classList.add("offline");
        } finally {
            inFlight = false;
        }
    }

    refreshTraffic();
    window.setInterval(refreshTraffic, 2000);
    document.addEventListener("visibilitychange", refreshTraffic);
})();
