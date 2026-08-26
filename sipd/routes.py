from flask import render_template_string

from sipd.auth import current_user, require_user


def register_routes(app):
    @app.get("/")
    @require_user
    def dashboard():
        return render_template_string("<h1>SIP-D</h1><p>{{ user.username }}</p>", user=current_user())

    @app.get("/login")
    def login():
        return "Login"
