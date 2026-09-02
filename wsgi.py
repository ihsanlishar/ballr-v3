# wsgi.py
"""
Production entry point. Gunicorn imports `app` from this file and
never executes api.py's __main__ block, so background workers must be
started explicitly here instead.
"""
from api import app, start_background_workers

start_background_workers()

if __name__ == "__main__":
    app.run()
