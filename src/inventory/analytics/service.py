"""Analytics service providing aggregated metrics, time-series analysis, and optimization data.

Interacts with the persistence layer (DatabaseManager and repositories) and delegates
mathematical routines to ``inventory.analytics.statistics``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from inventory.analytics import statistics as stats
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repositories.products import ProductRepository
from inventory.persistence.repositories.suppliers import SupplierRepository
from inventory.persistence.repositories.transactions import StockTransactionRepository


class AnalyticsService:
    """Core service for statistical inventory intelligence and reporting."""

    def __init__(
        self,
        database_manager: DatabaseManager,
        product_repository: ProductRepository,
        transaction_repository: StockTransactionRepository,
        supplier_repository: SupplierRepository,
    ) -> None:
        self.db = database_manager
        self.product_repo = product_repository
        self.transaction_repo = transaction_repository
        self.supplier_repo = supplier_repository

    # ------------------------------------------------------------------
    # 1. Inventory Overview
    # ------------------------------------------------------------------
    def get_inventory_overview(self) -> dict[str, Any]:
        """Summary of product catalog, stock availability, and inventory valuation."""
        products = self.product_repo.find_all()
        total_products = len(products)
        total_units = sum(p.quantity for p in products)
        total_valuation = sum(p.quantity * float(p.unit_price) for p in products)

        out_of_stock = [p for p in products if p.quantity == 0]
        low_stock = [p for p in products if 0 < p.quantity <= p.reorder_level]
        overstocked = [
            p
            for p in products
            if p.quantity >= max(50, p.reorder_level * 3)
        ]
        healthy_stock = [
            p
            for p in products
            if p.quantity > p.reorder_level and p not in overstocked
        ]

        # Category breakdown
        category_map: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "units": 0, "valuation": 0.0}
        )
        for p in products:
            c = category_map[p.category or "Uncategorized"]
            c["count"] += 1
            c["units"] += p.quantity
            c["valuation"] += p.quantity * float(p.unit_price)

        categories = [
            {
                "category": cat,
                "product_count": data["count"],
                "total_units": data["units"],
                "valuation": round(data["valuation"], 2),
            }
            for cat, data in sorted(category_map.items())
        ]

        return {
            "total_products": total_products,
            "total_available_stock": total_units,
            "total_inventory_valuation": round(total_valuation, 2),
            "out_of_stock_count": len(out_of_stock),
            "low_stock_count": len(low_stock),
            "overstocked_count": len(overstocked),
            "healthy_stock_count": len(healthy_stock),
            "categories": categories,
        }

    # ------------------------------------------------------------------
    # 2. Sales Analytics
    # ------------------------------------------------------------------
    def get_sales_analytics(self, days: int = 30) -> dict[str, Any]:
        """Tracks revenue, sales volume, AOV, daily trend, and top-selling products."""
        transactions = self.transaction_repo.find_all("sale")
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days) if days > 0 else None
        )

        filtered = []
        for t in transactions:
            ts = t.timestamp
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if cutoff is None or ts >= cutoff:
                filtered.append((ts, t))

        total_sales_count = len(filtered)
        total_units_sold = sum(t.quantity for _, t in filtered)
        total_revenue = sum(t.quantity * float(getattr(t, "sale_price", 0.0)) for _, t in filtered)
        aov = round(total_revenue / total_sales_count, 2) if total_sales_count > 0 else 0.0

        # Daily sales trend
        daily_revenue: dict[str, float] = defaultdict(float)
        daily_units: dict[str, int] = defaultdict(int)
        daily_orders: dict[str, int] = defaultdict(int)
        product_performance: dict[int, dict[str, Any]] = defaultdict(
            lambda: {"revenue": 0.0, "units": 0}
        )

        for ts, t in filtered:
            date_key = ts.strftime("%Y-%m-%d")
            rev = t.quantity * float(getattr(t, "sale_price", 0.0))
            daily_revenue[date_key] += rev
            daily_units[date_key] += t.quantity
            daily_orders[date_key] += 1
            product_performance[t.product_id]["revenue"] += rev
            product_performance[t.product_id]["units"] += t.quantity

        # Ensure timeline has entries for all dates in window
        timeline: list[dict[str, Any]] = []
        if days > 0:
            today = datetime.now(timezone.utc).date()
            for d in range(days - 1, -1, -1):
                cur_date = (today - timedelta(days=d)).strftime("%Y-%m-%d")
                timeline.append({
                    "date": cur_date,
                    "revenue": round(daily_revenue.get(cur_date, 0.0), 2),
                    "units_sold": daily_units.get(cur_date, 0),
                    "orders_count": daily_orders.get(cur_date, 0),
                })
        else:
            for cur_date in sorted(daily_revenue.keys()):
                timeline.append({
                    "date": cur_date,
                    "revenue": round(daily_revenue[cur_date], 2),
                    "units_sold": daily_units[cur_date],
                    "orders_count": daily_orders[cur_date],
                })

        # Rank best-selling products
        products_by_id = {p.product_id: p for p in self.product_repo.find_all()}
        top_products = []
        for pid, perf in sorted(
            product_performance.items(),
            key=lambda item: item[1]["revenue"],
            reverse=True,
        )[:10]:
            prod = products_by_id.get(pid)
            top_products.append({
                "product_id": pid,
                "name": prod.name if prod else f"Product #{pid}",
                "sku": prod.sku if prod else f"SKU-{pid}",
                "units_sold": perf["units"],
                "total_revenue": round(perf["revenue"], 2),
            })

        return {
            "period_days": days,
            "total_revenue": round(total_revenue, 2),
            "total_units_sold": total_units_sold,
            "total_sales_count": total_sales_count,
            "average_order_value": aov,
            "timeline": timeline,
            "top_products": top_products,
        }

    # ------------------------------------------------------------------
    # 3. Purchase Analytics
    # ------------------------------------------------------------------
    def get_purchase_analytics(self, days: int = 30) -> dict[str, Any]:
        """Tracks purchase expenditure, purchase volume, and procurement trends."""
        transactions = self.transaction_repo.find_all("purchase")
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days) if days > 0 else None
        )

        filtered = []
        for t in transactions:
            ts = t.timestamp
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if cutoff is None or ts >= cutoff:
                filtered.append((ts, t))

        total_purchase_count = len(filtered)
        total_units_purchased = sum(t.quantity for _, t in filtered)
        total_expenditure = sum(
            t.quantity * float(getattr(t, "unit_cost", 0.0)) for _, t in filtered
        )

        daily_spend: dict[str, float] = defaultdict(float)
        daily_units: dict[str, int] = defaultdict(int)
        for ts, t in filtered:
            date_key = ts.strftime("%Y-%m-%d")
            spend = t.quantity * float(getattr(t, "unit_cost", 0.0))
            daily_spend[date_key] += spend
            daily_units[date_key] += t.quantity

        timeline: list[dict[str, Any]] = []
        if days > 0:
            today = datetime.now(timezone.utc).date()
            for d in range(days - 1, -1, -1):
                cur_date = (today - timedelta(days=d)).strftime("%Y-%m-%d")
                timeline.append({
                    "date": cur_date,
                    "expenditure": round(daily_spend.get(cur_date, 0.0), 2),
                    "units_purchased": daily_units.get(cur_date, 0),
                })
        else:
            for cur_date in sorted(daily_spend.keys()):
                timeline.append({
                    "date": cur_date,
                    "expenditure": round(daily_spend[cur_date], 2),
                    "units_purchased": daily_units[cur_date],
                })

        return {
            "period_days": days,
            "total_expenditure": round(total_expenditure, 2),
            "total_units_purchased": total_units_purchased,
            "total_purchase_orders": total_purchase_count,
            "timeline": timeline,
        }

    # ------------------------------------------------------------------
    # 4. Supplier Analytics
    # ------------------------------------------------------------------
    def get_supplier_analytics(self) -> dict[str, Any]:
        """Evaluates supplier performance based on order volume, spend, and products."""
        suppliers = self.supplier_repo.find_all()
        purchases = self.transaction_repo.find_all("purchase")
        products = self.product_repo.find_all()

        products_per_supplier: dict[int, int] = defaultdict(int)
        for p in products:
            if p.supplier_id:
                products_per_supplier[p.supplier_id] += 1

        supplier_stats: dict[int, dict[str, Any]] = defaultdict(
            lambda: {"spend": 0.0, "units": 0, "order_count": 0}
        )
        for p_txn in purchases:
            s_id = getattr(p_txn, "supplier_id", None)
            if s_id:
                spend = p_txn.quantity * float(getattr(p_txn, "unit_cost", 0.0))
                supplier_stats[s_id]["spend"] += spend
                supplier_stats[s_id]["units"] += p_txn.quantity
                supplier_stats[s_id]["order_count"] += 1

        total_system_spend = sum(s["spend"] for s in supplier_stats.values())

        report = []
        for s in suppliers:
            st = supplier_stats[s.supplier_id]
            pct_spend = (
                round((st["spend"] / total_system_spend * 100.0), 1)
                if total_system_spend > 0
                else 0.0
            )
            report.append({
                "supplier_id": s.supplier_id,
                "name": s.name,
                "email": s.contact_email,
                "total_spend": round(st["spend"], 2),
                "units_supplied": st["units"],
                "purchase_orders": st["order_count"],
                "catalog_products": products_per_supplier.get(s.supplier_id, 0),
                "spend_share_pct": pct_spend,
            })

        report.sort(key=lambda x: x["total_spend"], reverse=True)
        return {
            "suppliers": report,
            "total_system_spend": round(total_system_spend, 2),
            "active_suppliers_count": len([r for r in report if r["purchase_orders"] > 0]),
        }

    # ------------------------------------------------------------------
    # 5. Stock Movement Analytics
    # ------------------------------------------------------------------
    def get_stock_movement_analytics(self, days: int = 30) -> dict[str, Any]:
        """Tracks stock additions (purchases), removals (sales), and turnover velocity."""
        txns = self.transaction_repo.find_all()
        products = self.product_repo.find_all()
        prod_map = {p.product_id: p for p in products}

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days) if days > 0 else None
        )

        inflows_daily: dict[str, int] = defaultdict(int)
        outflows_daily: dict[str, int] = defaultdict(int)
        product_sales_volume: dict[int, int] = defaultdict(int)
        total_inflow = 0
        total_outflow = 0

        for t in txns:
            ts = t.timestamp
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if cutoff is not None and ts < cutoff:
                continue

            date_str = ts.strftime("%Y-%m-%d")
            txn_type = getattr(t, "transaction_type", None) or (
                "sale" if hasattr(t, "sale_price") else "purchase"
            )

            if txn_type == "sale":
                outflows_daily[date_str] += t.quantity
                total_outflow += t.quantity
                product_sales_volume[t.product_id] += t.quantity
            elif txn_type == "purchase":
                inflows_daily[date_str] += t.quantity
                total_inflow += t.quantity

        # Build timeline
        timeline: list[dict[str, Any]] = []
        effective_days = max(1, days)
        today = datetime.now(timezone.utc).date()
        for d in range(effective_days - 1, -1, -1):
            cur_date = (today - timedelta(days=d)).strftime("%Y-%m-%d")
            in_qty = inflows_daily.get(cur_date, 0)
            out_qty = outflows_daily.get(cur_date, 0)
            timeline.append({
                "date": cur_date,
                "inflow": in_qty,
                "outflow": out_qty,
                "net_change": in_qty - out_qty,
            })

        # Calculate sales velocity & product classification
        velocity_list = []
        for p in products:
            sold_units = product_sales_volume.get(p.product_id, 0)
            daily_velocity = round(sold_units / effective_days, 2)
            velocity_list.append({
                "product_id": p.product_id,
                "name": p.name,
                "sku": p.sku,
                "current_stock": p.quantity,
                "units_sold": sold_units,
                "daily_velocity": daily_velocity,
            })

        velocity_list.sort(key=lambda x: x["daily_velocity"], reverse=True)
        fast_threshold = (
            velocity_list[len(velocity_list) // 4]["daily_velocity"]
            if len(velocity_list) >= 4
            else 0.5
        )

        for item in velocity_list:
            v = item["daily_velocity"]
            if v >= max(0.5, fast_threshold):
                item["velocity_category"] = "Fast-Moving"
            elif v > 0.05:
                item["velocity_category"] = "Moderate"
            elif v > 0:
                item["velocity_category"] = "Slow-Moving"
            else:
                item["velocity_category"] = "Non-Moving (Dead Stock)"

        # Inventory turnover ratio
        fin = self.get_financial_analytics()
        cogs = fin["cost_of_goods_sold"]
        avg_inv_val = fin["inventory_valuation"]
        turnover_ratio = (
            round(cogs / avg_inv_val, 2) if avg_inv_val > 0.0 else 0.0
        )
        dsi = (
            round(365.0 / turnover_ratio, 1) if turnover_ratio > 0.0 else 365.0
        )

        return {
            "period_days": days,
            "total_inflow_units": total_inflow,
            "total_outflow_units": total_outflow,
            "net_flow_units": total_inflow - total_outflow,
            "inventory_turnover_ratio": turnover_ratio,
            "days_sales_of_inventory": dsi,
            "timeline": timeline,
            "velocity_ranking": velocity_list,
        }

    # ------------------------------------------------------------------
    # 6. Financial Analytics
    # ------------------------------------------------------------------
    def get_financial_analytics(self) -> dict[str, Any]:
        """Provides insights into revenue, COGS, gross profit, and valuation."""
        sales = self.transaction_repo.find_all("sale")
        purchases = self.transaction_repo.find_all("purchase")
        products = self.product_repo.find_all()

        # Estimated cost per product (from latest purchase or product unit_price / 1.5 default)
        prod_unit_costs: dict[int, float] = {}
        for p_txn in purchases:
            pid = p_txn.product_id
            cost = float(getattr(p_txn, "unit_cost", 0.0))
            if cost > 0:
                prod_unit_costs[pid] = cost

        for p in products:
            if p.product_id not in prod_unit_costs:
                # Standard fallback assumption: cost is 60% of sale price
                prod_unit_costs[p.product_id] = round(float(p.unit_price) * 0.6, 2)

        total_revenue = sum(
            s.quantity * float(getattr(s, "sale_price", 0.0)) for s in sales
        )
        total_cogs = sum(
            s.quantity * prod_unit_costs.get(s.product_id, 0.0) for s in sales
        )
        gross_profit = total_revenue - total_cogs
        gross_margin = (
            round((gross_profit / total_revenue * 100.0), 1)
            if total_revenue > 0
            else 0.0
        )

        inventory_valuation = sum(
            p.quantity * float(p.unit_price) for p in products
        )

        total_purchase_spend = sum(
            p_txn.quantity * float(getattr(p_txn, "unit_cost", 0.0))
            for p_txn in purchases
        )

        return {
            "total_revenue": round(total_revenue, 2),
            "purchase_expenditure": round(total_purchase_spend, 2),
            "cost_of_goods_sold": round(total_cogs, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_margin_pct": gross_margin,
            "inventory_valuation": round(inventory_valuation, 2),
        }

    # ------------------------------------------------------------------
    # 7. Inventory Alerts & Monitoring
    # ------------------------------------------------------------------
    def get_inventory_alerts(self) -> dict[str, Any]:
        """Detects low stock, stockouts, overstock, and reorder point violations."""
        products = self.product_repo.find_all()
        reorder_recs = self.get_reorder_recommendations()
        rop_map = {r["product_id"]: r for r in reorder_recs["recommendations"]}

        alerts = []
        for p in products:
            rec = rop_map.get(p.product_id, {})
            rop = rec.get("reorder_point", p.reorder_level)

            if p.quantity == 0:
                alerts.append({
                    "product_id": p.product_id,
                    "name": p.name,
                    "sku": p.sku,
                    "level": "CRITICAL",
                    "type": "Stockout",
                    "message": f"{p.name} ({p.sku}) is completely OUT OF STOCK (0 units).",
                    "current_stock": 0,
                    "threshold": p.reorder_level,
                })
            elif p.quantity <= p.reorder_level:
                alerts.append({
                    "product_id": p.product_id,
                    "name": p.name,
                    "sku": p.sku,
                    "level": "WARNING",
                    "type": "Low Stock",
                    "message": f"Stock ({p.quantity}) is below reorder level ({p.reorder_level}).",
                    "current_stock": p.quantity,
                    "threshold": p.reorder_level,
                })
            elif p.quantity <= rop:
                alerts.append({
                    "product_id": p.product_id,
                    "name": p.name,
                    "sku": p.sku,
                    "level": "ACTION_REQUIRED",
                    "type": "Reorder Point Breached",
                    "message": f"Current stock ({p.quantity}) reached statistical ROP ({rop}).",
                    "current_stock": p.quantity,
                    "threshold": rop,
                })
            elif p.quantity >= max(200, p.reorder_level * 3):
                alerts.append({
                    "product_id": p.product_id,
                    "name": p.name,
                    "sku": p.sku,
                    "level": "INFO",
                    "type": "Overstocked",
                    "message": f"Excess inventory ({p.quantity} units) exceeding 3x reorder level.",
                    "current_stock": p.quantity,
                    "threshold": p.reorder_level * 3,
                })

        severity_order = {"CRITICAL": 0, "WARNING": 1, "ACTION_REQUIRED": 2, "INFO": 3}
        alerts.sort(key=lambda a: severity_order.get(a["level"], 4))

        return {
            "total_alerts": len(alerts),
            "critical_count": len([a for a in alerts if a["level"] == "CRITICAL"]),
            "warning_count": len([a for a in alerts if a["level"] == "WARNING"]),
            "action_count": len([a for a in alerts if a["level"] == "ACTION_REQUIRED"]),
            "info_count": len([a for a in alerts if a["level"] == "INFO"]),
            "alerts": alerts,
        }

    # ------------------------------------------------------------------
    # 8. ABC Inventory Analysis (Pareto Classification)
    # ------------------------------------------------------------------
    def get_abc_analysis(self) -> dict[str, Any]:
        """Calculates Pareto ABC classification based on Annual Consumption Value."""
        products = self.product_repo.find_all()
        sales = self.transaction_repo.find_all("sale")

        # Annualized units sold per product
        product_sold: dict[int, int] = defaultdict(int)
        for s in sales:
            product_sold[s.product_id] += s.quantity

        items = []
        for p in products:
            units = product_sold.get(p.product_id, 0)
            cost = float(p.unit_price) * 0.7  # or purchase unit cost
            items.append({
                "product_id": p.product_id,
                "name": p.name,
                "sku": p.sku,
                "category": p.category,
                "current_stock": p.quantity,
                "annual_units_sold": units,
                "unit_cost": round(cost, 2),
                "annual_consumption_value": round(units * cost, 2),
            })

        abc_report = stats.classify_abc_pareto(items, a_cutoff=0.80, b_cutoff=0.95)

        # Build Pareto curve data for Chart.js
        pareto_curve = []
        total_items = abc_report["summary"]["total_items"]
        for idx, it in enumerate(abc_report["items"], start=1):
            pareto_curve.append({
                "sku": it["sku"],
                "name": it["name"],
                "product_pct": round((idx / total_items * 100.0), 1) if total_items > 0 else 0.0,
                "cumulative_val_pct": it["cumulative_pct"],
                "class": it["abc_class"],
            })

        abc_report["pareto_curve"] = pareto_curve
        return abc_report

    # ------------------------------------------------------------------
    # 9. Demand Forecasting
    # ------------------------------------------------------------------
    def get_demand_forecast(
        self, product_id: int | None = None, horizon_days: int = 30
    ) -> dict[str, Any]:
        """Performs statistical demand forecasting (SMA, SES, Holt's linear trend)."""
        products = self.product_repo.find_all()
        if not products:
            return {
                "product": None,
                "historical": [],
                "sma_7": [],
                "ses": [],
                "forecast": [],
                "metrics": {},
            }

        # Select product
        selected_prod = None
        if product_id is not None:
            selected_prod = self.product_repo.find_by_id(product_id)
        if selected_prod is None:
            selected_prod = products[0]

        # Gather daily sales for past 60 days
        days_history = 60
        sales = self.transaction_repo.find_by_product(selected_prod.product_id)
        daily_units: dict[str, int] = defaultdict(int)

        for s in sales:
            txn_type = getattr(s, "transaction_type", None) or (
                "sale" if hasattr(s, "sale_price") else "purchase"
            )
            if txn_type != "sale":
                continue
            ts = s.timestamp
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            daily_units[ts.strftime("%Y-%m-%d")] += s.quantity

        today = datetime.now(timezone.utc).date()
        date_labels: list[str] = []
        history_values: list[float] = []

        for d in range(days_history - 1, -1, -1):
            date_str = (today - timedelta(days=d)).strftime("%Y-%m-%d")
            date_labels.append(date_str)
            history_values.append(float(daily_units.get(date_str, 0)))

        # Time series models
        sma_7 = stats.simple_moving_average(history_values, window=7)
        ses = stats.single_exponential_smoothing(history_values, alpha=0.25)
        holt_res = stats.holt_linear_trend_forecast(
            history_values, horizon=horizon_days, alpha=0.25, beta=0.15
        )

        # Future forecast dates
        future_dates = [
            (today + timedelta(days=h)).strftime("%Y-%m-%d")
            for h in range(1, horizon_days + 1)
        ]

        return {
            "product": {
                "product_id": selected_prod.product_id,
                "name": selected_prod.name,
                "sku": selected_prod.sku,
                "category": selected_prod.category,
                "current_stock": selected_prod.quantity,
            },
            "history_dates": date_labels,
            "actual_sales": history_values,
            "sma_7": sma_7,
            "ses": ses,
            "forecast_dates": future_dates,
            "forecast_values": holt_res["forecast"],
            "lower_ci": holt_res["lower_ci"],
            "upper_ci": holt_res["upper_ci"],
            "metrics": holt_res["metrics"],
        }

    # ------------------------------------------------------------------
    # 10. Smart Reorder Recommendations (ROP & EOQ)
    # ------------------------------------------------------------------
    def get_reorder_recommendations(
        self, service_level: float = 0.95
    ) -> dict[str, Any]:
        """Calculates Safety Stock, Reorder Point (ROP), and Economic Order Quantity (EOQ)."""
        products = self.product_repo.find_all()
        sales = self.transaction_repo.find_all("sale")

        # Map product sales by day (for std dev and mean demand calculation)
        prod_daily_demand: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for s in sales:
            ts = s.timestamp
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            date_str = ts.strftime("%Y-%m-%d")
            prod_daily_demand[s.product_id][date_str] += s.quantity

        today = datetime.now(timezone.utc).date()
        sample_days = 30

        recommendations = []
        urgent_count = 0

        for p in products:
            daily_dict = prod_daily_demand.get(p.product_id, {})
            # Build daily sample list for past 30 days
            demand_series = [
                float(daily_dict.get((today - timedelta(days=d)).strftime("%Y-%m-%d"), 0))
                for d in range(sample_days)
            ]

            mean_d = stats.calculate_mean(demand_series)
            std_d = stats.calculate_std_dev(demand_series)

            lead_time = 5.0  # default 5 days supplier lead time
            ss = stats.calculate_safety_stock(std_d, lead_time, service_level)
            rop = stats.calculate_reorder_point(mean_d, lead_time, ss)

            annual_d = mean_d * 365.0
            unit_price = float(p.unit_price)
            holding_cost = max(0.5, unit_price * 0.20)
            eoq = stats.calculate_eoq(
                annual_demand=annual_d,
                order_cost=50.0,
                holding_cost_per_unit=holding_cost,
            )

            current_stock = p.quantity
            if current_stock <= rop:
                status = "REORDER_NOW"
                badge_class = "danger"
                urgent_count += 1
                recommended_order_qty = max(eoq, (rop - current_stock) + eoq)
            elif current_stock <= rop * 1.25:
                status = "APPROACHING"
                badge_class = "warning"
                recommended_order_qty = eoq
            else:
                status = "OPTIMAL"
                badge_class = "success"
                recommended_order_qty = 0

            recommendations.append({
                "product_id": p.product_id,
                "name": p.name,
                "sku": p.sku,
                "category": p.category,
                "current_stock": current_stock,
                "mean_daily_demand": round(mean_d, 2),
                "std_dev_demand": round(std_d, 2),
                "lead_time_days": lead_time,
                "safety_stock": ss,
                "reorder_point": rop,
                "economic_order_quantity": eoq,
                "recommended_order_qty": recommended_order_qty,
                "status": status,
                "badge_class": badge_class,
            })

        # Sort with urgent reorders first, then lowest stock relative to ROP
        recommendations.sort(
            key=lambda r: (
                0 if r["status"] == "REORDER_NOW" else (1 if r["status"] == "APPROACHING" else 2),
                r["current_stock"] - r["reorder_point"],
            )
        )

        return {
            "service_level": service_level,
            "urgent_reorder_count": urgent_count,
            "total_products_evaluated": len(products),
            "recommendations": recommendations,
        }
