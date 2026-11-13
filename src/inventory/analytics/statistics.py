"""Statistical algorithms and mathematical models for inventory optimization.

Includes:
- Summary demand statistics (mean, sample variance, standard deviation)
- Time-series demand forecasting (Simple Moving Average, Exponential Smoothing, Holt's linear trend)
- Forecast accuracy evaluation (MAE, MSE, RMSE, MAPE)
- Inventory control equations (Safety Stock SS, Reorder Point ROP, Economic Order Quantity EOQ)
- ABC Inventory Classification via Pareto Analysis (80/20 Rule)
"""

from __future__ import annotations

import math
from typing import Any


def calculate_mean(values: list[float]) -> float:
    """Compute the arithmetic mean of a list of numeric values."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def calculate_std_dev(values: list[float], sample: bool = True) -> float:
    """Compute the sample (or population) standard deviation.

    Uses Bessel's correction (N - 1) for sample standard deviation by default.
    """
    n = len(values)
    if n <= 1:
        return 0.0
    mean = calculate_mean(values)
    variance_sum = sum((x - mean) ** 2 for x in values)
    divisor = (n - 1) if sample else n
    return math.sqrt(variance_sum / divisor)


def simple_moving_average(series: list[float], window: int = 7) -> list[float | None]:
    """Calculate simple moving average (SMA) for a time series.

    Returns a list of the same length as ``series``, where indices with
    insufficient history (< window - 1) contain None.
    """
    if window <= 0:
        raise ValueError("Window size must be greater than zero.")
    result: list[float | None] = []
    for i in range(len(series)):
        if i < window - 1:
            result.append(None)
        else:
            window_slice = series[i - window + 1 : i + 1]
            result.append(round(sum(window_slice) / window, 2))
    return result


def single_exponential_smoothing(
    series: list[float], alpha: float = 0.25
) -> list[float]:
    """Apply Single Exponential Smoothing (SES) to a time series.

    Formula:
        ŷ_{t+1} = α * y_t + (1 - α) * ŷ_t
    """
    if not series:
        return []
    if not (0.0 < alpha <= 1.0):
        raise ValueError("Smoothing factor alpha must be in the range (0.0, 1.0].")

    smoothed: list[float] = [round(series[0], 2)]
    for t in range(1, len(series)):
        val = alpha * series[t] + (1.0 - alpha) * smoothed[t - 1]
        smoothed.append(round(val, 2))
    return smoothed


def holt_linear_trend_forecast(
    series: list[float],
    horizon: int = 30,
    alpha: float = 0.25,
    beta: float = 0.15,
) -> dict[str, Any]:
    """Holt's Linear Exponential Smoothing (Level + Trend) with future horizon projection.

    Parameters:
        series: Historical observations [y_1, y_2, ..., y_T].
        horizon: Number of future periods to forecast.
        alpha: Level smoothing factor (0 < alpha <= 1).
        beta: Trend smoothing factor (0 < beta <= 1).

    Returns:
        dict containing:
            - 'fitted': in-sample smoothed values
            - 'forecast': future projected values for horizon periods
            - 'lower_ci': lower 95% confidence interval bound for forecast
            - 'upper_ci': upper 95% confidence interval bound for forecast
            - 'metrics': accuracy metrics (MAE, RMSE, MAPE)
    """
    if not series:
        return {
            "fitted": [],
            "forecast": [0.0] * horizon,
            "lower_ci": [0.0] * horizon,
            "upper_ci": [0.0] * horizon,
            "metrics": {"mae": 0.0, "rmse": 0.0, "mape": 0.0},
        }

    n = len(series)
    if n == 1:
        val = round(series[0], 2)
        return {
            "fitted": [val],
            "forecast": [val] * horizon,
            "lower_ci": [val] * horizon,
            "upper_ci": [val] * horizon,
            "metrics": {"mae": 0.0, "rmse": 0.0, "mape": 0.0},
        }

    # Initialize level and trend
    level = series[0]
    trend = series[1] - series[0]

    fitted: list[float] = [round(level, 2)]

    for t in range(1, n):
        prev_level = level
        level = alpha * series[t] + (1.0 - alpha) * (prev_level + trend)
        trend = beta * (level - prev_level) + (1.0 - beta) * trend
        fitted.append(round(level + trend, 2))

    # Evaluate in-sample accuracy metrics
    metrics = calculate_forecast_metrics(series, fitted)
    rmse = metrics["rmse"] or 1.0

    # Project horizon
    forecast: list[float] = []
    lower_ci: list[float] = []
    upper_ci: list[float] = []

    for h in range(1, horizon + 1):
        f = max(0.0, level + h * trend)
        ci_spread = 1.96 * rmse * math.sqrt(h)
        forecast.append(round(f, 2))
        lower_ci.append(round(max(0.0, f - ci_spread), 2))
        upper_ci.append(round(f + ci_spread, 2))

    return {
        "fitted": fitted,
        "forecast": forecast,
        "lower_ci": lower_ci,
        "upper_ci": upper_ci,
        "metrics": metrics,
    }


def calculate_forecast_metrics(
    actual: list[float], predicted: list[float]
) -> dict[str, float]:
    """Calculate standard forecast accuracy metrics: MAE, MSE, RMSE, MAPE."""
    n = min(len(actual), len(predicted))
    if n == 0:
        return {"mae": 0.0, "mse": 0.0, "rmse": 0.0, "mape": 0.0}

    abs_errors = [abs(a - p) for a, p in zip(actual[:n], predicted[:n])]
    sq_errors = [(a - p) ** 2 for a, p in zip(actual[:n], predicted[:n])]

    mae = sum(abs_errors) / n
    mse = sum(sq_errors) / n
    rmse = math.sqrt(mse)

    mape_terms = [
        abs(a - p) / a for a, p in zip(actual[:n], predicted[:n]) if a > 0.0001
    ]
    mape = (sum(mape_terms) / len(mape_terms) * 100.0) if mape_terms else 0.0

    return {
        "mae": round(mae, 2),
        "mse": round(mse, 2),
        "rmse": round(rmse, 2),
        "mape": round(mape, 2),
    }


def get_z_factor_for_service_level(service_level: float) -> float:
    """Return the normal distribution Z-score for the desired cycle service level."""
    if service_level >= 0.999:
        return 3.090
    if service_level >= 0.99:
        return 2.326
    if service_level >= 0.98:
        return 2.054
    if service_level >= 0.95:
        return 1.645
    if service_level >= 0.90:
        return 1.282
    if service_level >= 0.85:
        return 1.036
    if service_level >= 0.80:
        return 0.842
    return 0.674  # 75% or default fallback


def calculate_safety_stock(
    std_dev_daily_demand: float,
    lead_time_days: float,
    service_level: float = 0.95,
) -> int:
    """Calculate Safety Stock (SS).

    Formula:
        SS = Z * σ_d * sqrt(L)
    """
    if std_dev_daily_demand <= 0 or lead_time_days <= 0:
        return 0
    z = get_z_factor_for_service_level(service_level)
    ss = z * std_dev_daily_demand * math.sqrt(lead_time_days)
    return max(0, math.ceil(ss))


def calculate_reorder_point(
    mean_daily_demand: float,
    lead_time_days: float,
    safety_stock: int,
) -> int:
    """Calculate Reorder Point (ROP).

    Formula:
        ROP = (μ_d * L) + SS
    """
    lead_demand = max(0.0, mean_daily_demand) * max(0.0, lead_time_days)
    return math.ceil(lead_demand + safety_stock)


def calculate_eoq(
    annual_demand: float,
    order_cost: float = 50.0,
    holding_cost_per_unit: float = 5.0,
) -> int:
    """Calculate Economic Order Quantity (EOQ).

    Formula:
        EOQ = sqrt( (2 * D * S) / H )

    Parameters:
        annual_demand (D): Units expected to be sold per year.
        order_cost (S): Fixed cost incurred per purchase order.
        holding_cost_per_unit (H): Annual cost of holding one unit in stock.
    """
    if annual_demand <= 0 or order_cost <= 0:
        return 0
    h = max(0.1, holding_cost_per_unit)
    eoq = math.sqrt((2.0 * annual_demand * order_cost) / h)
    return max(1, math.ceil(eoq))


def classify_abc_pareto(
    items: list[dict[str, Any]],
    a_cutoff: float = 0.80,
    b_cutoff: float = 0.95,
) -> dict[str, Any]:
    """Perform Pareto ABC Inventory Classification based on Annual Consumption Value.

    Each item dict must include at least:
        - 'product_id': int
        - 'name': str
        - 'sku': str
        - 'annual_units_sold': float or int
        - 'unit_cost': float or Decimal or 'annual_consumption_value': float

    Returns a report dict containing:
        - 'items': list of items annotated with consumption value, rank, cumulative %, and class ('A', 'B', 'C')
        - 'summary': count and percentage breakdowns per class
    """
    if not items:
        return {
            "items": [],
            "summary": {
                "A": {"count": 0, "pct_items": 0.0, "total_value": 0.0, "pct_value": 0.0},
                "B": {"count": 0, "pct_items": 0.0, "total_value": 0.0, "pct_value": 0.0},
                "C": {"count": 0, "pct_items": 0.0, "total_value": 0.0, "pct_value": 0.0},
                "total_items": 0,
                "total_value": 0.0,
            },
        }

    # Prepare item consumption values
    enriched: list[dict[str, Any]] = []
    total_val = 0.0

    for it in items:
        c_val = float(it.get("annual_consumption_value", 0.0))
        if c_val <= 0.0:
            units = float(it.get("annual_units_sold", 0.0))
            cost = float(it.get("unit_cost", 0.0))
            c_val = round(units * cost, 2)
        total_val += c_val
        item_copy = dict(it)
        item_copy["consumption_value"] = c_val
        enriched.append(item_copy)

    # Sort descending by consumption value
    enriched.sort(key=lambda x: x["consumption_value"], reverse=True)

    running_sum = 0.0
    total_count = len(enriched)

    summary = {
        "A": {"count": 0, "pct_items": 0.0, "total_value": 0.0, "pct_value": 0.0},
        "B": {"count": 0, "pct_items": 0.0, "total_value": 0.0, "pct_value": 0.0},
        "C": {"count": 0, "pct_items": 0.0, "total_value": 0.0, "pct_value": 0.0},
        "total_items": total_count,
        "total_value": round(total_val, 2),
    }

    for rank, it in enumerate(enriched, start=1):
        running_sum += it["consumption_value"]
        cum_pct = (running_sum / total_val * 100.0) if total_val > 0 else 100.0
        cum_pct = min(100.0, round(cum_pct, 2))

        # Assign Category
        if cum_pct <= a_cutoff * 100.0 or rank == 1:
            tier = "A"
        elif cum_pct <= b_cutoff * 100.0:
            tier = "B"
        else:
            tier = "C"

        it["rank"] = rank
        it["cumulative_value"] = round(running_sum, 2)
        it["cumulative_pct"] = cum_pct
        it["abc_class"] = tier

        summary[tier]["count"] += 1
        summary[tier]["total_value"] += it["consumption_value"]

    # Compute percentage summaries
    for tier in ("A", "B", "C"):
        t_count = summary[tier]["count"]
        t_val = summary[tier]["total_value"]
        summary[tier]["pct_items"] = round((t_count / total_count * 100.0), 1) if total_count > 0 else 0.0
        summary[tier]["pct_value"] = round((t_val / total_val * 100.0), 1) if total_val > 0 else 0.0
        summary[tier]["total_value"] = round(t_val, 2)

    return {"items": enriched, "summary": summary}
