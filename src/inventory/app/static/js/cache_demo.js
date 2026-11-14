(() => {
    const svgNamespace = "http://www.w3.org/2000/svg";
    const form = document.getElementById("benchmarkForm");
    const iterationsInput = document.getElementById("iterationsInput");
    const runButton = document.getElementById("runButton");
    const message = document.getElementById("message");
    const results = document.getElementById("results");

    function showMessage(text, isError = false) {
        message.textContent = text;
        message.classList.toggle("error", isError);
    }

    function milliseconds(value) {
        if (value < 1) {
            return `${(value * 1000).toFixed(1)} µs`;
        }
        return `${value.toFixed(3)} ms`;
    }

    function percent(value) {
        return `${value.toFixed(1)}%`;
    }

    function svgElement(name, attributes = {}) {
        const element = document.createElementNS(svgNamespace, name);
        for (const [attribute, value] of Object.entries(attributes)) {
            element.setAttribute(attribute, String(value));
        }
        return element;
    }

    function drawResponseChart(data) {
        const left = 64;
        const top = 28;
        const width = 720;
        const height = 258;
        const maxRequests = data.iterations;
        const maxTime = Math.max(
            ...data.series.flatMap((point) => [
                point.database_total_ms,
                point.cache_total_ms,
            ]),
            1,
        );
        const roughStep = maxTime / 4;
        const stepMagnitude = 10 ** Math.floor(Math.log10(roughStep));
        const step = Math.ceil(roughStep / stepMagnitude) * stepMagnitude;
        const yMaximum = step * 4;
        const x = (requests) => left + (requests / maxRequests) * width;
        const y = (millisecondsValue) =>
            top + height - (millisecondsValue / yMaximum) * height;
        const grid = document.getElementById("chartGrid");
        const points = document.getElementById("chartPoints");
        const databaseCoordinates = [];
        const cacheCoordinates = [];
        grid.replaceChildren();
        points.replaceChildren();

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
            label.textContent = value < 10 ? value.toFixed(1) : value.toFixed(0);
            grid.append(label);
        }

        const middleRequest = Math.round(maxRequests / 2);
        for (const [requestCount, anchor] of [
            [0, "start"],
            [middleRequest, "middle"],
            [maxRequests, "end"],
        ]) {
            const tick = svgElement("text", {
                x: x(requestCount),
                y: top + height + 22,
                class: "chart-tick",
                "text-anchor": anchor,
            });
            tick.textContent = requestCount;
            grid.append(tick);
        }

        for (const sample of data.series) {
            const xPosition = x(sample.requests);
            const databaseY = y(sample.database_total_ms);
            const cacheY = y(sample.cache_total_ms);
            databaseCoordinates.push(`${xPosition},${databaseY}`);
            cacheCoordinates.push(`${xPosition},${cacheY}`);
            for (const [lineY, total, label] of [
                [databaseY, sample.database_total_ms, "Database"],
                [cacheY, sample.cache_total_ms, "Cache"],
            ]) {
                const marker = svgElement("circle", {
                    cx: xPosition,
                    cy: lineY,
                    r: 2.5,
                    class: label === "Database" ? "database-point" : "cache-point",
                });
                const tooltip = svgElement("title");
                tooltip.textContent =
                    `${sample.requests} requests — ${label}: ${milliseconds(total)}`;
                marker.append(tooltip);
                points.append(marker);
            }
        }
        document.getElementById("databaseLine").setAttribute(
            "points",
            databaseCoordinates.join(" "),
        );
        document.getElementById("cacheLine").setAttribute(
            "points",
            cacheCoordinates.join(" "),
        );

        const breakEvenLine = document.getElementById("breakEvenLine");
        const breakEvenLabel = document.getElementById("breakEvenLabel");
        if (data.break_even_reads !== null && data.break_even_reads <= maxRequests) {
            const breakEvenX = x(data.break_even_reads);
            breakEvenLine.setAttribute("x1", breakEvenX);
            breakEvenLine.setAttribute("x2", breakEvenX);
            breakEvenLine.setAttribute("visibility", "visible");
            breakEvenLabel.setAttribute("x", Math.min(breakEvenX + 6, left + width - 8));
            breakEvenLabel.setAttribute("y", top + 14);
            breakEvenLabel.setAttribute("visibility", "visible");
            breakEvenLabel.textContent = `Break-even ≈ ${data.break_even_reads}`;
        } else {
            breakEvenLine.setAttribute("visibility", "hidden");
            breakEvenLabel.setAttribute("visibility", "hidden");
            breakEvenLabel.textContent = "";
        }

        document.getElementById("responseChart").setAttribute(
            "aria-label",
            `Across ${maxRequests} requests per path, database time was ${milliseconds(data.database.total_ms)}; cache fill plus reads took ${milliseconds(data.cache.total_ms)}.`,
        );
        const timeSaved = data.database.total_ms - data.cache.total_ms;
        document.getElementById("chartCallout").textContent =
            timeSaved >= 0
                ? `At ${maxRequests} requests, the cache path saved ${milliseconds(timeSaved)} in total (${percent(data.improvement_percent)} less time).`
                : `At ${maxRequests} requests, cache fill plus reads took ${milliseconds(Math.abs(timeSaved))} longer. More repeated reads may be needed to break even.`;
    }

    function renderBenchmark(data) {
        const dbAverage = data.database.average_ms;
        const cacheAverage = data.cache.average_ms;
        const change = data.improvement_percent;
        const improvementCard = document.getElementById("improvementCard");

        document.getElementById("databaseAvg").textContent = milliseconds(dbAverage);
        document.getElementById("cacheAvg").textContent = milliseconds(cacheAverage);
        document.getElementById("improvement").textContent =
            `${change >= 0 ? "+" : ""}${percent(change)}`;
        document.getElementById("improvementCaption").textContent =
            change >= 0 ? "faster than database-only reads" : "slower than database-only reads";
        improvementCard.classList.toggle("faster", change >= 0);
        improvementCard.classList.toggle("slower", change < 0);

        document.getElementById("policyPill").textContent =
            `Policy: ${data.cache.policy.toUpperCase()}`;
        document.getElementById("hitRate").textContent = percent(data.cache.hit_rate * 100);
        document.getElementById("localHits").textContent =
            `${data.cache.local_hits} / ${data.cache.reads}`;
        document.getElementById("breakEven").textContent =
            data.break_even_reads === null
                ? "Not reached in this run"
                : `${data.break_even_reads} repeated reads`;
        document.getElementById("firstRead").textContent =
            `${milliseconds(data.cache.first_read_ms)} (${data.cache.first_read_status})`;
        document.getElementById("readCounts").textContent = `${data.iterations}`;
        document.getElementById("measurementNote").textContent =
            `Measurements alternate paths to reduce ordering bias. Cache line includes the initial ${milliseconds(data.cache.first_read_ms)} cache fill; later reads include hits and any Redis revalidation.`;
        drawResponseChart(data);
        results.hidden = false;
    }

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        runButton.disabled = true;
        runButton.textContent = "Measuring…";
        showMessage("Running read-only measurements; larger runs can take several seconds…");
        results.hidden = true;
        try {
            const token = localStorage.getItem("smobhar_session_token")
                || localStorage.getItem("token")
                || "";
            const response = await fetch("/api/cache/benchmark", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    ...(token ? { "Authorization": `Bearer ${token}` } : {}),
                },
                body: JSON.stringify({
                    iterations: Number(iterationsInput.value),
                }),
            });
            const data = await response.json().catch(() => null);
            if (!response.ok) {
                throw new Error(data?.description || `Benchmark failed (HTTP ${response.status}).`);
            }
            if (!data || !Array.isArray(data.series) || data.series.length < 2) {
                throw new Error("The server returned incomplete benchmark data. Restart the app and reload this page.");
            }
            renderBenchmark(data);
            showMessage("Comparison complete.");
        } catch (error) {
            showMessage(
                error instanceof TypeError
                    ? "Could not reach the benchmark API. Confirm the app is running, then reload this page."
                    : error.message,
                true,
            );
        } finally {
            runButton.disabled = false;
            runButton.textContent = "Run comparison";
        }
    });
})();
