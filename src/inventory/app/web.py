"""Flask route definitions for the SMOभर frontend."""

from __future__ import annotations

from flask import Flask, redirect, render_template, url_for


def register_page_routes(app: Flask) -> None:
    """Register all frontend page routes onto the Flask app."""

    @app.get("/")
    def index():
        return redirect(url_for("login"))

    @app.get("/login")
    @app.get("/login.html")
    def login():
        return render_template("login.html")

    @app.get("/signup")
    @app.get("/signup.html")
    def signup():
        return render_template("signup.html")

    @app.get("/workspace")
    @app.get("/smobhar.html")
    def workspace():
        return render_template("smobhar.html")

    @app.get("/smobhar1")
    @app.get("/workspace1")
    @app.get("/smobhar1.html")
    def workspace1():
        return render_template("smobhar1.html")

    @app.get("/home")
    @app.get("/home.html")
    def home():
        return render_template("home.html")

    @app.get("/about")
    @app.get("/about.html")
    def about():
        return render_template("about.html")

    @app.get("/admin")
    @app.get("/admiview.html")
    def admin():
        return render_template("admiview.html")

    @app.get("/admin-roles")
    @app.get("/adminrole.html")
    @app.get("/adminroles.html")
    def admin_roles():
        return render_template("adminrole.html")

    @app.get("/permissions")
    @app.get("/permission.html")
    @app.get("/permissions.html")
    def permissions():
        return render_template("permission.html")

    @app.get("/suppliers")
    @app.get("/supplier.html")
    @app.get("/suppliers.html")
    def suppliers():
        return render_template("supplier.html")

    @app.get("/cache-demo")
    def cache_demo():
        return render_template("cache_demo.html")

    @app.get("/cache-traffic")
    def cache_traffic_page():
        return render_template("cache_traffic.html")

    @app.get("/analytics")
    def analytics_page():
        return render_template("analytics.html")
