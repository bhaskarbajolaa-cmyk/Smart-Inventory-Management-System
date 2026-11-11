(() => {
    const svgNamespace = "http://www.w3.org/2000/svg";
    const form = document.getElementById("benchmarkForm");
    const iterationsInput = document.getElementById("iterationsInput");
    const runButton = document.getElementById("runButton");
    const message = document.getElementById("message");
    const results = document.getElementById("results");
    const traffic = {
        runs: 0,
        databaseReads: 0,
        cacheReads: 0,
        points: [],
    };

    document.getElementById("resetTrafficButton").addEventListener("click", () => {
        traffic.runs = 0;
        traffic.databaseReads = 0;
        traffic.cacheReads = 0;
        traffic.points = [];
        drawTrafficChart();
    });

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

    function recordTraffic(data) {
        traffic.runs += 1;
        traffic.databaseReads += data.database.reads;
        traffic.cacheReads += data.cache.reads;
        traffic.points.push({
            run: traffic.runs,
            databaseReads: traffic.databaseReads,
            cacheReads: traffic.cacheReads,
        });
        if (traffic.points.length > 20) {
            traffic.points.shift();
        }
        drawTrafficChart();
    }

    function drawTrafficChart() {
        document.getElementById("trafficRuns").textContent = traffic.runs;
        document.getElementById("trafficDatabase").textContent = traffic.databaseReads;
        document.getElementById("trafficCache").textContent = traffic.cacheReads;

        const left = 64;
        const top = 24;
        const width = 720;
        const height = 196;
        const firstRun = traffic.points[0]?.run ?? 0;
        const lastRun = traffic.points.at(-1)?.run ?? 1;
        const maxReads = Math.max(
            (traffic.points.at(-1)?.databaseReads ?? 0)
                + (traffic.points.at(-1)?.cacheReads ?? 0),
            1,
        );
        const yStep = Math.max(1, Math.ceil(maxReads / 4));
        const yMaximum = yStep * 4;
        const x = (run) =>
            traffic.points.length < 2
                ? left + width / 2
                : left + ((run - firstRun) / Math.max(1, lastRun - firstRun)) * width;
        const y = (count) => top + height - (count / yMaximum) * height;
        const grid = document.getElementById("trafficChartGrid");
        const markers = document.getElementById("trafficChartPoints");
        const totalCoordinates = [];
        grid.replaceChildren();
        markers.replaceChildren();

        for (let tick = 0; tick <= 4; tick += 1) {
            const value = yStep * tick;
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

        const runTicks = traffic.points.length < 2
            ? [lastRun]
            : [...new Set([firstRun, Math.round((firstRun + lastRun) / 2), lastRun])];
        for (const [index, run] of runTicks.entries()) {
            const label = svgElement("text", {
                x: x(run),
                y: top + height + 22,
                class: "chart-tick",
                "text-anchor": index === 0 ? "start" : index === runTicks.length - 1 ? "end" : "middle",
            });
            label.textContent = run;
            grid.append(label);
        }

        for (const point of traffic.points) {
            const xPosition = x(point.run);
            const total = point.databaseReads + point.cacheReads;
            const yPosition = y(total);
            totalCoordinates.push(`${xPosition},${yPosition}`);
            const marker = svgElement("circle", {
                cx: xPosition,
                cy: yPosition,
                r: 3,
                class: "cache-point",
            });
            const tooltip = svgElement("title");
            tooltip.textContent =
                `Run ${point.run}: ${total.toLocaleString()} total read requests (${point.databaseReads} database, ${point.cacheReads} cache)`;
            marker.append(tooltip);
            markers.append(marker);
        }

        document.getElementById("trafficTotalLine").setAttribute(
            "points",
            totalCoordinates.join(" "),
        );
        document.getElementById("trafficChart").setAttribute(
            "aria-label",
            `${traffic.runs} completed benchmark runs since this page opened: ${traffic.databaseReads + traffic.cacheReads} total read requests, ${traffic.databaseReads} on the database path and ${traffic.cacheReads} on the cache path.`,
        );
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
        recordTraffic(data);
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
            const response = await fetch("/api/cache/benchmark", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
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
