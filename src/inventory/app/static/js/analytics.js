/**
 * SMOभर Analytics & Statistical Intelligence Interactive Controller
 */

document.addEventListener("DOMContentLoaded", () => {
    // ------------------------------------------------------------------
    // Authentication & State
    // ------------------------------------------------------------------
    const token = localStorage.getItem("token") || "";
    if (!token) {
        window.location.href = "/login";
        return;
    }

    const headers = {
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json",
    };

    let currentDays = 30;
    let currentServiceLevel = 0.95;
    let currentProductId = null;
    let currentHorizon = 30;
    let abcItemsCache = [];
    let activeAbcFilter = "ALL";

    // Chart.js instance registry
    const charts = {
        category: null,
        salesTrend: null,
        movement: null,
        pareto: null,
        forecast: null,
    };

    // ------------------------------------------------------------------
    // Formatters
    // ------------------------------------------------------------------
    const formatCurrency = (val) => {
        const num = parseFloat(val) || 0;
        return "₹" + num.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    };

    const formatNumber = (val) => {
        const num = parseFloat(val) || 0;
        return num.toLocaleString("en-IN");
    };

    // Check user role for admin link
    const storedUser = localStorage.getItem("user");
    if (storedUser) {
        try {
            const userObj = JSON.parse(storedUser);
            if (userObj.role_id === 1) {
                const navAdmin = document.getElementById("navAdminLink");
                if (navAdmin) navAdmin.style.display = "inline-block";
            }
        } catch (_) {}
    }

    // Logout
    const logoutBtn = document.getElementById("logoutBtn");
    if (logoutBtn) {
        logoutBtn.addEventListener("click", () => {
            localStorage.removeItem("token");
            localStorage.removeItem("user");
            window.location.href = "/login";
        });
    }

    // ------------------------------------------------------------------
    // API Fetcher Helper
    // ------------------------------------------------------------------
    async function apiFetch(endpoint) {
        try {
            const res = await fetch(endpoint, { headers });
            if (res.status === 401) {
                localStorage.removeItem("token");
                window.location.href = "/login";
                return null;
            }
            if (!res.ok) {
                console.error(`API error on ${endpoint}: ${res.statusText}`);
                return null;
            }
            return await res.json();
        } catch (err) {
            console.error(`Fetch failure on ${endpoint}:`, err);
            return null;
        }
    }

    // ------------------------------------------------------------------
    // 1. Overview & Financials Loader
    // ------------------------------------------------------------------
    async function loadOverview() {
        const overview = await apiFetch("/api/analytics/overview");
        const financials = await apiFetch("/api/analytics/financials");
        const sales = await apiFetch(`/api/analytics/sales?days=${currentDays}`);
        const movement = await apiFetch(`/api/analytics/stock-movement?days=${currentDays}`);
        const alerts = await apiFetch("/api/analytics/alerts");
        const reorders = await apiFetch(`/api/analytics/reorder-recommendations?service_level=${currentServiceLevel}`);

        if (overview) {
            document.getElementById("kpiValuation").textContent = formatCurrency(overview.total_inventory_valuation);
            document.getElementById("kpiTotalUnits").textContent = `${formatNumber(overview.total_available_stock)} units in stock across ${overview.total_products} items`;

            document.getElementById("healthyCount").textContent = overview.healthy_stock_count;
            document.getElementById("lowCount").textContent = overview.low_stock_count;
            document.getElementById("outCount").textContent = overview.out_of_stock_count;
            document.getElementById("overCount").textContent = overview.overstocked_count;

            renderCategoryChart(overview.categories || []);
        }

        if (financials && sales) {
            document.getElementById("kpiRevenue").textContent = formatCurrency(sales.total_revenue);
            document.getElementById("kpiGrossMargin").textContent = `Gross Margin: ${financials.gross_margin_pct}% (Profit: ${formatCurrency(financials.gross_profit)})`;

            document.getElementById("finCOGS").textContent = formatCurrency(financials.cost_of_goods_sold);
            document.getElementById("finProfit").textContent = formatCurrency(financials.gross_profit);
            document.getElementById("finSpend").textContent = formatCurrency(financials.purchase_expenditure);
        }

        if (movement) {
            document.getElementById("kpiTurnover").textContent = `${movement.inventory_turnover_ratio}x`;
            document.getElementById("kpiDSI").textContent = `Days Sales of Inv: ${movement.days_sales_of_inventory} days`;
        }

        if (alerts && reorders) {
            document.getElementById("kpiAlertCount").textContent = alerts.total_alerts;
            document.getElementById("kpiUrgentReorder").textContent = `${reorders.urgent_reorder_count} urgent reorders required`;
        }

        if (sales && sales.top_products) {
            renderTopProducts(sales.top_products);
        }
    }

    function renderCategoryChart(categories) {
        const ctx = document.getElementById("categoryChart")?.getContext("2d");
        if (!ctx) return;

        if (charts.category) charts.category.destroy();

        const labels = categories.map((c) => c.category);
        const data = categories.map((c) => c.valuation);

        charts.category = new Chart(ctx, {
            type: "doughnut",
            data: {
                labels,
                datasets: [
                    {
                        data,
                        backgroundColor: [
                            "#8b5cf6",
                            "#06b6d4",
                            "#10b981",
                            "#f59e0b",
                            "#ec4899",
                            "#6366f1",
                        ],
                        borderColor: "#120e21",
                        borderWidth: 3,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: "bottom",
                        labels: { color: "#9ca3af", font: { size: 12 } },
                    },
                    tooltip: {
                        callbacks: {
                            label: (context) => ` ${context.label}: ${formatCurrency(context.raw)}`,
                        },
                    },
                },
            },
        });
    }

    function renderTopProducts(products) {
        const tbody = document.querySelector("#topProductsTable tbody");
        if (!tbody) return;
        tbody.innerHTML = "";

        if (products.length === 0) {
            tbody.innerHTML = `<tr><td colspan="4" class="text-center">No sales recorded yet.</td></tr>`;
            return;
        }

        products.forEach((p) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${p.name}</strong></td>
                <td><code>${p.sku}</code></td>
                <td>${formatNumber(p.units_sold)}</td>
                <td><strong class="text-success">${formatCurrency(p.total_revenue)}</strong></td>
            `;
            tbody.appendChild(tr);
        });
    }

    // ------------------------------------------------------------------
    // 2. Sales & Stock Movement Loader
    // ------------------------------------------------------------------
    async function loadSalesAndMovement() {
        const sales = await apiFetch(`/api/analytics/sales?days=${currentDays}`);
        const movement = await apiFetch(`/api/analytics/stock-movement?days=${currentDays}`);

        if (sales && sales.timeline) {
            renderSalesTrendChart(sales.timeline);
        }

        if (movement) {
            if (movement.timeline) renderMovementChart(movement.timeline);
            if (movement.velocity_ranking) renderVelocityTable(movement.velocity_ranking);
        }
    }

    function renderSalesTrendChart(timeline) {
        const ctx = document.getElementById("salesTrendChart")?.getContext("2d");
        if (!ctx) return;
        if (charts.salesTrend) charts.salesTrend.destroy();

        const labels = timeline.map((t) => t.date);
        const revenues = timeline.map((t) => t.revenue);
        const units = timeline.map((t) => t.units_sold);

        charts.salesTrend = new Chart(ctx, {
            type: "line",
            data: {
                labels,
                datasets: [
                    {
                        label: "Revenue (₹)",
                        data: revenues,
                        borderColor: "#8b5cf6",
                        backgroundColor: "rgba(139, 92, 246, 0.15)",
                        fill: true,
                        tension: 0.3,
                        yAxisID: "yRev",
                    },
                    {
                        label: "Units Sold",
                        data: units,
                        borderColor: "#06b6d4",
                        borderDash: [5, 5],
                        backgroundColor: "transparent",
                        tension: 0.3,
                        yAxisID: "yUnits",
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { ticks: { color: "#9ca3af" }, grid: { color: "rgba(255,255,255,0.05)" } },
                    yRev: {
                        type: "linear",
                        position: "left",
                        ticks: { color: "#8b5cf6", callback: (v) => "₹" + v },
                        grid: { color: "rgba(255,255,255,0.05)" },
                    },
                    yUnits: {
                        type: "linear",
                        position: "right",
                        ticks: { color: "#06b6d4" },
                        grid: { drawOnChartArea: false },
                    },
                },
                plugins: {
                    legend: { labels: { color: "#ffffff" } },
                },
            },
        });
    }

    function renderMovementChart(timeline) {
        const ctx = document.getElementById("movementChart")?.getContext("2d");
        if (!ctx) return;
        if (charts.movement) charts.movement.destroy();

        const labels = timeline.map((t) => t.date);
        const inflows = timeline.map((t) => t.inflow);
        const outflows = timeline.map((t) => t.outflow);

        charts.movement = new Chart(ctx, {
            type: "bar",
            data: {
                labels,
                datasets: [
                    {
                        label: "Stock Inflow (Purchases)",
                        data: inflows,
                        backgroundColor: "rgba(16, 185, 129, 0.75)",
                    },
                    {
                        label: "Stock Outflow (Sales)",
                        data: outflows,
                        backgroundColor: "rgba(239, 68, 68, 0.75)",
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { ticks: { color: "#9ca3af" }, grid: { color: "rgba(255,255,255,0.05)" } },
                    y: { ticks: { color: "#9ca3af" }, grid: { color: "rgba(255,255,255,0.05)" } },
                },
                plugins: {
                    legend: { labels: { color: "#ffffff" } },
                },
            },
        });
    }

    function renderVelocityTable(velocityList) {
        const tbody = document.querySelector("#velocityTable tbody");
        if (!tbody) return;
        tbody.innerHTML = "";

        velocityList.forEach((v) => {
            const tr = document.createElement("tr");
            let badgeClass = "info";
            if (v.velocity_category === "Fast-Moving") badgeClass = "success";
            else if (v.velocity_category === "Slow-Moving") badgeClass = "warning";
            else if (v.velocity_category.startsWith("Non-Moving")) badgeClass = "danger";

            tr.innerHTML = `
                <td><strong>${v.name}</strong> <small>(${v.sku})</small></td>
                <td>${v.current_stock}</td>
                <td>${v.units_sold}</td>
                <td><strong>${v.daily_velocity} / day</strong></td>
                <td><span class="status-badge ${badgeClass}">${v.velocity_category}</span></td>
            `;
            tbody.appendChild(tr);
        });
    }

    // ------------------------------------------------------------------
    // 3. ABC Pareto Analysis Loader
    // ------------------------------------------------------------------
    async function loadAbcAnalysis() {
        const abc = await apiFetch("/api/analytics/abc");
        if (!abc) return;

        abcItemsCache = abc.items || [];

        // Summary cards
        const s = abc.summary;
        document.getElementById("abcSummaryA").textContent = `${s.A.count} items (${s.A.pct_items}%) generating ${formatCurrency(s.A.total_value)} (${s.A.pct_value}%)`;
        document.getElementById("abcSummaryB").textContent = `${s.B.count} items (${s.B.pct_items}%) generating ${formatCurrency(s.B.total_value)} (${s.B.pct_value}%)`;
        document.getElementById("abcSummaryC").textContent = `${s.C.count} items (${s.C.pct_items}%) generating ${formatCurrency(s.C.total_value)} (${s.C.pct_value}%)`;

        renderParetoChart(abc.pareto_curve || []);
        filterAndRenderAbcTable();
    }

    function renderParetoChart(curveData) {
        const ctx = document.getElementById("paretoChart")?.getContext("2d");
        if (!ctx) return;
        if (charts.pareto) charts.pareto.destroy();

        const labels = curveData.map((d) => d.sku);
        const cumValues = curveData.map((d) => d.cumulative_val_pct);

        charts.pareto = new Chart(ctx, {
            type: "line",
            data: {
                labels,
                datasets: [
                    {
                        label: "Cumulative Consumption Value %",
                        data: cumValues,
                        borderColor: "#8b5cf6",
                        backgroundColor: "rgba(139, 92, 246, 0.2)",
                        fill: true,
                        tension: 0.25,
                        pointRadius: 4,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { ticks: { color: "#9ca3af" }, grid: { color: "rgba(255,255,255,0.05)" } },
                    y: {
                        min: 0,
                        max: 100,
                        ticks: { color: "#9ca3af", callback: (v) => v + "%" },
                        grid: { color: "rgba(255,255,255,0.05)" },
                    },
                },
                plugins: {
                    legend: { labels: { color: "#ffffff" } },
                },
            },
        });
    }

    function filterAndRenderAbcTable() {
        const tbody = document.querySelector("#abcTable tbody");
        if (!tbody) return;
        tbody.innerHTML = "";

        const filtered = abcItemsCache.filter((item) => {
            if (activeAbcFilter === "ALL") return true;
            return item.abc_class === activeAbcFilter;
        });

        if (filtered.length === 0) {
            tbody.innerHTML = `<tr><td colspan="9" class="text-center">No products match class ${activeAbcFilter}.</td></tr>`;
            return;
        }

        filtered.forEach((it) => {
            const tr = document.createElement("tr");
            let badgeClass = "success";
            if (it.abc_class === "B") badgeClass = "info";
            if (it.abc_class === "C") badgeClass = "warning";

            tr.innerHTML = `
                <td>#${it.rank}</td>
                <td><strong>${it.name}</strong></td>
                <td><code>${it.sku}</code></td>
                <td>${it.category || "General"}</td>
                <td>${it.current_stock}</td>
                <td>${formatNumber(it.annual_units_sold)}</td>
                <td>${formatCurrency(it.consumption_value)}</td>
                <td>${it.cumulative_pct}%</td>
                <td><span class="status-badge ${badgeClass}">Class ${it.abc_class}</span></td>
            `;
            tbody.appendChild(tr);
        });
    }

    // Filter pill click listeners
    document.querySelectorAll("[data-abc-filter]").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll("[data-abc-filter]").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            activeAbcFilter = btn.dataset.abcFilter;
            filterAndRenderAbcTable();
        });
    });

    // ------------------------------------------------------------------
    // 4. Demand Forecasting & Smart Reorder Loader
    // ------------------------------------------------------------------
    async function loadForecastAndReorders() {
        await populateProductDropdown();
        await fetchAndRenderForecast();
        await loadReorderRecommendations();
    }

    async function populateProductDropdown() {
        const sel = document.getElementById("forecastProductSelect");
        if (!sel || sel.options.length > 1) return;

        const overview = await apiFetch("/api/analytics/overview");
        const productsResp = await apiFetch("/api/products");
        if (!productsResp) return;

        sel.innerHTML = "";
        productsResp.forEach((p, idx) => {
            const opt = document.createElement("option");
            opt.value = p.product_id;
            opt.textContent = `${p.name} (${p.sku})`;
            if (idx === 0 && !currentProductId) {
                currentProductId = p.product_id;
                opt.selected = true;
            }
            sel.appendChild(opt);
        });

        sel.addEventListener("change", () => {
            currentProductId = parseInt(sel.value, 10);
            fetchAndRenderForecast();
        });

        const horizSel = document.getElementById("forecastHorizonSelect");
        if (horizSel) {
            horizSel.addEventListener("change", () => {
                currentHorizon = parseInt(horizSel.value, 10);
                fetchAndRenderForecast();
            });
        }
    }

    async function fetchAndRenderForecast() {
        const pidParam = currentProductId ? `&product_id=${currentProductId}` : "";
        const data = await apiFetch(`/api/analytics/forecast?horizon=${currentHorizon}${pidParam}`);
        if (!data) return;

        // Update chart title & product info
        if (data.product) {
            document.getElementById("forecastChartTitle").textContent = `Demand Forecast for ${data.product.name} (${data.product.sku}) - In Stock: ${data.product.current_stock}`;
        }

        // Metrics
        if (data.metrics) {
            document.getElementById("metricMAE").textContent = data.metrics.mae;
            document.getElementById("metricRMSE").textContent = data.metrics.rmse;
            document.getElementById("metricMAPE").textContent = data.metrics.mape + "%";
        }

        renderForecastChart(data);
    }

    function renderForecastChart(data) {
        const ctx = document.getElementById("forecastChart")?.getContext("2d");
        if (!ctx) return;
        if (charts.forecast) charts.forecast.destroy();

        // Combine labels
        const allLabels = [...data.history_dates, ...data.forecast_dates];
        const histCount = data.history_dates.length;
        const forecastCount = data.forecast_dates.length;

        // Actual sales array padded with nulls for future
        const actualSeries = [...data.actual_sales, ...new Array(forecastCount).fill(null)];

        // 7-day SMA padded with nulls
        const smaSeries = [...data.sma_7, ...new Array(forecastCount).fill(null)];

        // Exponential smoothing padded with nulls
        const sesSeries = [...data.ses, ...new Array(forecastCount).fill(null)];

        // Forecast projection padded with nulls for history
        // Connect history and forecast at the transition point
        const lastActual = data.actual_sales[histCount - 1] || 0;
        const forecastSeries = [
            ...new Array(histCount - 1).fill(null),
            lastActual,
            ...data.forecast_values,
        ];
        const lowerCiSeries = [
            ...new Array(histCount - 1).fill(null),
            lastActual,
            ...data.lower_ci,
        ];
        const upperCiSeries = [
            ...new Array(histCount - 1).fill(null),
            lastActual,
            ...data.upper_ci,
        ];

        charts.forecast = new Chart(ctx, {
            type: "line",
            data: {
                labels: allLabels,
                datasets: [
                    {
                        label: "Actual Sales Demand",
                        data: actualSeries,
                        borderColor: "#38bdf8",
                        backgroundColor: "#38bdf8",
                        pointRadius: 3,
                        pointHoverRadius: 6,
                        tension: 0.2,
                    },
                    {
                        label: "7-Day Moving Avg (SMA)",
                        data: smaSeries,
                        borderColor: "#f59e0b",
                        borderDash: [4, 4],
                        pointRadius: 0,
                        tension: 0.3,
                    },
                    {
                        label: "Exponential Smoothing (SES)",
                        data: sesSeries,
                        borderColor: "#10b981",
                        pointRadius: 0,
                        tension: 0.3,
                    },
                    {
                        label: "Holt's Linear Forecast",
                        data: forecastSeries,
                        borderColor: "#a855f7",
                        borderWidth: 3,
                        pointRadius: 2,
                        tension: 0.2,
                    },
                    {
                        label: "95% Upper CI",
                        data: upperCiSeries,
                        borderColor: "rgba(168, 85, 247, 0.3)",
                        backgroundColor: "rgba(168, 85, 247, 0.12)",
                        fill: "+1",
                        pointRadius: 0,
                        tension: 0.2,
                    },
                    {
                        label: "95% Lower CI",
                        data: lowerCiSeries,
                        borderColor: "rgba(168, 85, 247, 0.3)",
                        pointRadius: 0,
                        tension: 0.2,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { ticks: { color: "#9ca3af" }, grid: { color: "rgba(255,255,255,0.05)" } },
                    y: {
                        ticks: { color: "#9ca3af" },
                        grid: { color: "rgba(255,255,255,0.05)" },
                        title: { display: true, text: "Daily Units", color: "#9ca3af" },
                    },
                },
                plugins: {
                    legend: { labels: { color: "#ffffff" } },
                },
            },
        });
    }

    async function loadReorderRecommendations() {
        const data = await apiFetch(`/api/analytics/reorder-recommendations?service_level=${currentServiceLevel}`);
        const tbody = document.querySelector("#reorderTable tbody");
        if (!tbody || !data) return;
        tbody.innerHTML = "";

        data.recommendations.forEach((r) => {
            const tr = document.createElement("tr");
            let badgeText = "Optimal";
            if (r.status === "REORDER_NOW") badgeText = "REORDER NOW";
            else if (r.status === "APPROACHING") badgeText = "Approaching ROP";

            tr.innerHTML = `
                <td><strong>${r.name}</strong> <small>(${r.sku})</small></td>
                <td><strong>${r.current_stock}</strong></td>
                <td>${r.mean_daily_demand}</td>
                <td>${r.std_dev_demand}</td>
                <td>${r.safety_stock}</td>
                <td><strong class="text-warning">${r.reorder_point}</strong></td>
                <td>${r.economic_order_quantity}</td>
                <td>${r.recommended_order_qty > 0 ? `<strong class="text-urgent">${r.recommended_order_qty} units</strong>` : "—"}</td>
                <td><span class="status-badge ${r.badge_class}">${badgeText}</span></td>
            `;
            tbody.appendChild(tr);
        });
    }

    const serviceLevelSel = document.getElementById("serviceLevelSelect");
    if (serviceLevelSel) {
        serviceLevelSel.addEventListener("change", () => {
            currentServiceLevel = parseFloat(serviceLevelSel.value);
            loadReorderRecommendations();
        });
    }

    // ------------------------------------------------------------------
    // 5. Inventory Alerts Loader
    // ------------------------------------------------------------------
    async function loadAlerts() {
        const data = await apiFetch("/api/analytics/alerts");
        const feed = document.getElementById("alertsFeed");
        if (!feed || !data) return;

        document.getElementById("alertCritTag").textContent = `${data.critical_count} Critical`;
        document.getElementById("alertWarnTag").textContent = `${data.warning_count} Warning`;
        document.getElementById("alertActionTag").textContent = `${data.action_count} Action Required`;
        document.getElementById("alertInfoTag").textContent = `${data.info_count} Info`;

        feed.innerHTML = "";
        if (data.alerts.length === 0) {
            feed.innerHTML = `<div class="alert-item-card"><div class="alert-content"><h5>✅ All Systems Healthy</h5><p>No low-stock, stockout, or anomalous inventory conditions detected.</p></div></div>`;
            return;
        }

        data.alerts.forEach((a) => {
            const card = document.createElement("div");
            card.className = `alert-item-card level-${a.level}`;
            card.innerHTML = `
                <div class="alert-content">
                    <h5>${a.type}: ${a.name} (<code>${a.sku}</code>)</h5>
                    <p>${a.message}</p>
                </div>
                <div class="alert-meta">
                    <div>Current: <strong>${a.current_stock}</strong></div>
                    <div>Threshold: <strong>${a.threshold}</strong></div>
                </div>
            `;
            feed.appendChild(card);
        });
    }

    // ------------------------------------------------------------------
    // Navigation Tabs & Period Controls
    // ------------------------------------------------------------------
    document.querySelectorAll(".analytics-tab").forEach((tabBtn) => {
        tabBtn.addEventListener("click", () => {
            document.querySelectorAll(".analytics-tab").forEach((b) => b.classList.remove("active"));
            document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));

            tabBtn.classList.add("active");
            const targetPane = document.getElementById(tabBtn.dataset.tab);
            if (targetPane) targetPane.classList.add("active");

            // Trigger tab specific load / chart resize
            const tabId = tabBtn.dataset.tab;
            if (tabId === "tab-overview") {
                loadOverview();
                if (charts.category) charts.category.resize();
            } else if (tabId === "tab-sales") {
                loadSalesAndMovement();
                if (charts.salesTrend) charts.salesTrend.resize();
                if (charts.movement) charts.movement.resize();
            } else if (tabId === "tab-abc") {
                loadAbcAnalysis();
                if (charts.pareto) charts.pareto.resize();
            } else if (tabId === "tab-forecast") {
                loadForecastAndReorders();
                if (charts.forecast) charts.forecast.resize();
            } else if (tabId === "tab-alerts") {
                loadAlerts();
            }
        });
    });

    document.querySelectorAll(".period-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".period-btn").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            currentDays = parseInt(btn.dataset.days, 10);
            loadOverview();
            loadSalesAndMovement();
        });
    });

    // Initial Load
    loadOverview();
    loadSalesAndMovement();
    loadAbcAnalysis();
    loadForecastAndReorders();
    loadAlerts();
});
