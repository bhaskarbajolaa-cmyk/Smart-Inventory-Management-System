"""Flask route definitions for the SMOभर frontend."""

from __future__ import annotations

from flask import Flask, redirect, render_template, url_for


def register_page_routes(app: Flask) -> None:
    """Register all frontend page routes onto the Flask app."""

    @app.get("/")
    def index():
        return redirect(url_for("login"))

    @app.get("/login")
    def login():
        return render_template("login.html")

    @app.get("/signup")
    def signup():
        return render_template("signup.html")

    @app.get("/workspace")
    def workspace():
        return render_template("smobhar.html")

    @app.get("/home")
    def home():
        return render_template("home.html")

    @app.get("/about")
    def about():
        return render_template("about.html")

    @app.get("/admin")
    def admin():
        return render_template("admiview.html")

    @app.get("/cache-demo")
    def cache_demo():
        return render_template("cache_demo.html")
