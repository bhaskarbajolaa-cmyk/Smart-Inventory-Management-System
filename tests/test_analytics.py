"""Unit and integration tests for the Analytics & Statistical Analysis Subsystem."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from inventory.analytics import statistics as stats
from inventory.analytics.service import AnalyticsService
from inventory.app.main import build_application
from inventory.app.server import create_app
from inventory.catalog.models import Product, Supplier
from inventory.transactions.models import Purchase, Sale


class TestPureStatisticsEngine(unittest.TestCase):
    """Test suite for mathematical and statistical routines in statistics.py."""

    def test_calculate_mean(self) -> None:
        self.assertEqual(stats.calculate_mean([]), 0.0)
        self.assertEqual(stats.calculate_mean([10.0]), 10.0)
        self.assertAlmostEqual(stats.calculate_mean([10.0, 20.0, 30.0]), 20.0)

    def test_calculate_std_dev(self) -> None:
        self.assertEqual(stats.calculate_std_dev([]), 0.0)
        self.assertEqual(stats.calculate_std_dev([5.0]), 0.0)
        # Sample standard deviation of [10, 20, 30] -> mean=20, variance=((100+0+100)/2)=100, s=10
        self.assertAlmostEqual(stats.calculate_std_dev([10.0, 20.0, 30.0], sample=True), 10.0)
        # Population standard deviation of [10, 20, 30] -> variance=(200/3), s=8.16496
        self.assertAlmostEqual(
            stats.calculate_std_dev([10.0, 20.0, 30.0], sample=False),
            8.1649658,
            places=4,
        )

    def test_simple_moving_average(self) -> None:
        series = [10.0, 20.0, 30.0, 40.0, 50.0]
        sma_3 = stats.simple_moving_average(series, window=3)
        self.assertEqual(len(sma_3), 5)
        self.assertIsNone(sma_3[0])
        self.assertIsNone(sma_3[1])
        self.assertEqual(sma_3[2], 20.0)  # (10+20+30)/3
        self.assertEqual(sma_3[3], 30.0)  # (20+30+40)/3
        self.assertEqual(sma_3[4], 40.0)  # (30+40+50)/3

        with self.assertRaises(ValueError):
            stats.simple_moving_average(series, window=0)

    def test_single_exponential_smoothing(self) -> None:
        series = [100.0, 110.0, 120.0, 130.0]
        smoothed = stats.single_exponential_smoothing(series, alpha=0.5)
        self.assertEqual(len(smoothed), 4)
        self.assertEqual(smoothed[0], 100.0)
        # t=1: 0.5 * 110 + 0.5 * 100 = 105.0
        self.assertEqual(smoothed[1], 105.0)
        # t=2: 0.5 * 120 + 0.5 * 105 = 112.5
        self.assertEqual(smoothed[2], 112.5)

        with self.assertRaises(ValueError):
            stats.single_exponential_smoothing(series, alpha=1.5)

    def test_holt_linear_trend_forecast(self) -> None:
        series = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
        res = stats.holt_linear_trend_forecast(series, horizon=7, alpha=0.3, beta=0.2)
        self.assertEqual(len(res["fitted"]), len(series))
        self.assertEqual(len(res["forecast"]), 7)
        self.assertEqual(len(res["lower_ci"]), 7)
        self.assertEqual(len(res["upper_ci"]), 7)
        # Projected values should continue upward
        self.assertGreater(res["forecast"][-1], res["forecast"][0])
        # Upper CI should be strictly >= lower CI
        for low, high in zip(res["lower_ci"], res["upper_ci"]):
            self.assertGreaterEqual(high, low)

    def test_calculate_forecast_metrics(self) -> None:
        actual = [10.0, 20.0, 30.0]
        pred = [12.0, 18.0, 30.0]
        m = stats.calculate_forecast_metrics(actual, pred)
        # errors: [2, 2, 0] -> MAE = 4/3 = 1.33
        self.assertEqual(m["mae"], 1.33)
        # sq errors: [4, 4, 0] -> MSE = 8/3 = 2.67
        self.assertEqual(m["mse"], 2.67)
        self.assertAlmostEqual(m["rmse"], 1.63, places=2)

    def test_calculate_safety_stock(self) -> None:
        # std_dev=5, lead_time=4, Z for 95% = 1.645 -> SS = ceil(1.645 * 5 * 2) = ceil(16.45) = 17
        ss = stats.calculate_safety_stock(std_dev_daily_demand=5.0, lead_time_days=4.0, service_level=0.95)
        self.assertEqual(ss, 17)

        # Zero std_dev or lead_time returns 0
        self.assertEqual(stats.calculate_safety_stock(0.0, 5.0), 0)
        self.assertEqual(stats.calculate_safety_stock(5.0, 0.0), 0)

    def test_calculate_reorder_point(self) -> None:
        # mean=10, lead_time=5, safety_stock=17 -> ROP = ceil(50 + 17) = 67
        rop = stats.calculate_reorder_point(mean_daily_demand=10.0, lead_time_days=5.0, safety_stock=17)
        self.assertEqual(rop, 67)

    def test_calculate_eoq(self) -> None:
        # D = 1000, S = 50, H = 2 -> EOQ = ceil(sqrt(2 * 1000 * 50 / 2)) = ceil(sqrt(50000)) = ceil(223.6) = 224
        eoq = stats.calculate_eoq(annual_demand=1000.0, order_cost=50.0, holding_cost_per_unit=2.0)
        self.assertEqual(eoq, 224)

        # Non-positive annual demand returns 0
        self.assertEqual(stats.calculate_eoq(annual_demand=0.0), 0)

    def test_classify_abc_pareto(self) -> None:
        items = [
            {"product_id": 1, "sku": "A1", "name": "Big Seller", "annual_consumption_value": 8000.0},
            {"product_id": 2, "sku": "B1", "name": "Medium Seller", "annual_consumption_value": 1500.0},
            {"product_id": 3, "sku": "C1", "name": "Small Seller", "annual_consumption_value": 500.0},
        ]
        # Total value = 10,000. Big Seller is 80% (A), Medium is next 15% (B), Small is last 5% (C)
        res = stats.classify_abc_pareto(items, a_cutoff=0.80, b_cutoff=0.95)
        self.assertEqual(len(res["items"]), 3)
        self.assertEqual(res["items"][0]["abc_class"], "A")
        self.assertEqual(res["items"][1]["abc_class"], "B")
        self.assertEqual(res["items"][2]["abc_class"], "C")
        self.assertEqual(res["summary"]["total_value"], 10000.0)
        self.assertEqual(res["summary"]["A"]["count"], 1)
        self.assertEqual(res["summary"]["B"]["count"], 1)
        self.assertEqual(res["summary"]["C"]["count"], 1)


class TestAnalyticsServiceAndAPI(unittest.TestCase):
    """Integration test suite verifying AnalyticsService queries and REST endpoints."""

    def setUp(self) -> None:
        self.services = build_application()
        self.app = create_app(self.services)
        self.client = self.app.test_client()

        # Admin login for session token
        resp = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin1234"})
        if resp.status_code != 200:
            resp = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        self.token = resp.json["session_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_overview_endpoint(self) -> None:
        # Unauthenticated request returns 401
        unauth_resp = self.client.get("/api/analytics/overview")
        self.assertEqual(unauth_resp.status_code, 401)

        # Authenticated request returns 200 with complete metrics
        resp = self.client.get("/api/analytics/overview", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json
        self.assertIn("total_products", data)
        self.assertIn("total_available_stock", data)
        self.assertIn("total_inventory_valuation", data)
        self.assertIn("out_of_stock_count", data)
        self.assertIn("low_stock_count", data)
        self.assertIn("categories", data)
        self.assertIsInstance(data["categories"], list)

    def test_sales_analytics_endpoint(self) -> None:
        resp = self.client.get("/api/analytics/sales?days=30", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json
        self.assertIn("total_revenue", data)
        self.assertIn("total_units_sold", data)
        self.assertIn("average_order_value", data)
        self.assertIn("timeline", data)
        self.assertIn("top_products", data)
        self.assertGreaterEqual(len(data["timeline"]), 1)

    def test_purchase_analytics_endpoint(self) -> None:
        resp = self.client.get("/api/analytics/purchases?days=60", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json
        self.assertIn("total_expenditure", data)
        self.assertIn("total_units_purchased", data)
        self.assertIn("timeline", data)

    def test_supplier_analytics_endpoint(self) -> None:
        resp = self.client.get("/api/analytics/suppliers", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json
        self.assertIn("suppliers", data)
        self.assertIn("total_system_spend", data)
        self.assertIsInstance(data["suppliers"], list)

    def test_stock_movement_endpoint(self) -> None:
        resp = self.client.get("/api/analytics/stock-movement?days=30", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json
        self.assertIn("total_inflow_units", data)
        self.assertIn("total_outflow_units", data)
        self.assertIn("inventory_turnover_ratio", data)
        self.assertIn("days_sales_of_inventory", data)
        self.assertIn("velocity_ranking", data)

    def test_financials_endpoint(self) -> None:
        resp = self.client.get("/api/analytics/financials", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json
        self.assertIn("total_revenue", data)
        self.assertIn("cost_of_goods_sold", data)
        self.assertIn("gross_profit", data)
        self.assertIn("gross_margin_pct", data)
        self.assertIn("inventory_valuation", data)

    def test_alerts_endpoint(self) -> None:
        resp = self.client.get("/api/analytics/alerts", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json
        self.assertIn("total_alerts", data)
        self.assertIn("alerts", data)
        self.assertIsInstance(data["alerts"], list)

    def test_abc_endpoint(self) -> None:
        resp = self.client.get("/api/analytics/abc", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json
        self.assertIn("items", data)
        self.assertIn("summary", data)
        self.assertIn("pareto_curve", data)
        self.assertIn("A", data["summary"])
        self.assertIn("B", data["summary"])
        self.assertIn("C", data["summary"])

    def test_demand_forecast_endpoint(self) -> None:
        resp = self.client.get("/api/analytics/forecast?horizon=14", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json
        self.assertIn("product", data)
        self.assertIn("actual_sales", data)
        self.assertIn("forecast_values", data)
        self.assertIn("lower_ci", data)
        self.assertIn("upper_ci", data)
        self.assertEqual(len(data["forecast_values"]), 14)

    def test_reorder_recommendations_endpoint(self) -> None:
        resp = self.client.get("/api/analytics/reorder-recommendations?service_level=0.95", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json
        self.assertIn("recommendations", data)
        self.assertIn("urgent_reorder_count", data)
        self.assertIsInstance(data["recommendations"], list)
        if data["recommendations"]:
            rec = data["recommendations"][0]
            self.assertIn("reorder_point", rec)
            self.assertIn("safety_stock", rec)
            self.assertIn("economic_order_quantity", rec)
            self.assertIn("status", rec)

    def test_web_analytics_page(self) -> None:
        resp = self.client.get("/analytics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Analytics", resp.data)
