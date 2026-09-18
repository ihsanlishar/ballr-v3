"""One worker owns the background feeds and SQLite; no preloading."""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5051')}"
workers = 1
threads = 4
timeout = 120
preload_app = False
accesslog = '-'
errorlog = '-'
