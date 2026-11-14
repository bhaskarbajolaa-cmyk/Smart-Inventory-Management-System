"""Flask page routes for the SMOभर frontend, with server-side access guards."""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from flask import (
    Flask,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)

SESSION_COOKIE = "smobhar_session_token"


def _services() -> Any:
    """Return the application service container, if the app was built with one."""
    return current_app.extensions.get("services")


def _authenticated_session() -> tuple[Any, Any]:
    """Resolve the session named by the HttpOnly cookie, or ``(None, None)``."""
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None, None
    services = _services()
    authentication = getattr(services, "authentication", None)
    if authentication is None:
        return None, None
    session = authentication.get_session(token)
    if session is None or session.is_expired():
        return None, None
    return services, session


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Redirect anonymous visitors to the login page."""

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        _services_container, session = _authenticated_session()
        if session is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Allow only users whose role carries the ``ADMIN`` permission.

    The check is permission-based rather than ``role_id == 1`` so a custom role
    granted ``ADMIN`` through the role matrix is treated the same way as the
    seeded Admin role (and the API's own ``ADMIN`` checks stay consistent).
    """

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        services, session = _authenticated_session()
        if session is None:
            return redirect(url_for("login"))
        user = services.user_repository.find_by_id(session.user_id)
        if user is None:
            return redirect(url_for("login"))
        role = services.role_repository.find_by_id(user.role_id)
        if role is None or not user.has_permission("ADMIN", role):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def permission_required(code: str) -> Callable[..., Any]:
    """Render the blocking state when the signed-in user lacks ``code``.

    A user with no permissions at all also fails this check, so the table,
    navigation and action buttons are never sent to the browser.
    """

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            services, session = _authenticated_session()
            if session is None:
                return redirect(url_for("login"))
            user = services.user_repository.find_by_id(session.user_id)
            if user is None:
                return redirect(url_for("login"))
            role = services.role_repository.find_by_id(user.role_id)
            if role is None or not user.has_permission(code, role):
                return render_template("no_permissions.html", user=user), 403
            return view(*args, **kwargs)

        return wrapped

    return decorator


def register_page_routes(app: Flask) -> None:
    """Register all frontend page routes onto the Flask app."""

    # -- public ---------------------------------------------------------

    @app.get("/")
    def index():
        return redirect(url_for("login"))

    @app.get("/login.html")
    @app.get("/login")
    def login():
        return render_template("login.html")

    @app.get("/signup")
    @app.get("/signup.html")
    def signup():
        return render_template("signup.html")

    # -- any signed-in user ---------------------------------------------

    @app.get("/workspace")
    @app.get("/smobhar.html")
    @permission_required("VIEW")
    def workspace():
        return render_template("smobhar.html")

    @app.get("/smobhar1")
    @app.get("/workspace1")
    @app.get("/smobhar1.html")
    @permission_required("VIEW")
    def workspace1():
        return render_template("smobhar1.html")

    @app.get("/home")
    @app.get("/home.html")
    @permission_required("VIEW")
    def home():
        return render_template("home.html")

    @app.get("/about")
    @app.get("/about.html")
    @login_required
    def about():
        return render_template("about.html")

    @app.get("/analytics")
    @permission_required("VIEW")
    def analytics_page():
        return render_template("analytics.html")

    # -- administrators --------------------------------------------------

    @app.get("/admin")
    @app.get("/admiview.html")
    @admin_required
    def admin():
        return render_template("admiview.html")

    @app.get("/admin-roles")
    @app.get("/adminrole.html")
    @app.get("/adminroles.html")
    @admin_required
    def admin_roles():
        return render_template("adminrole.html")

    @app.get("/permissions")
    @app.get("/permission.html")
    @app.get("/permissions.html")
    @admin_required
    def permissions():
        return render_template("permission.html")

    @app.get("/suppliers")
    @app.get("/supplier.html")
    @app.get("/suppliers.html")
    @admin_required
    def suppliers():
        return render_template("supplier.html")

    @app.get("/cache-demo")
    @admin_required
    def cache_demo():
        return render_template("cache_demo.html")

    @app.get("/cache-traffic")
    @admin_required
    def cache_traffic_page():
        return render_template("cache_traffic.html")
