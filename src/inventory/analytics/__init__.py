"""Analytics and statistical optimization package for SMOभर inventory management."""

from inventory.analytics.service import AnalyticsService
from inventory.analytics.statistics import (
    calculate_eoq,
    calculate_mean,
    calculate_reorder_point,
    calculate_safety_stock,
    calculate_std_dev,
    classify_abc_pareto,
    holt_linear_trend_forecast,
    simple_moving_average,
    single_exponential_smoothing,
)

__all__ = [
    "AnalyticsService",
    "calculate_eoq",
    "calculate_mean",
    "calculate_reorder_point",
    "calculate_safety_stock",
    "calculate_std_dev",
    "classify_abc_pareto",
    "holt_linear_trend_forecast",
    "simple_moving_average",
    "single_exponential_smoothing",
]
