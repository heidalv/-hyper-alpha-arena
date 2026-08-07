"""Heidalv Alpha Arena Desktop App Configuration"""

import os
from pathlib import Path

# Paths
ROOT_DIR = Path(__file__).parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
FRONTEND_DIST_DIR = FRONTEND_DIR / "dist"
BACKEND_STATIC_DIR = BACKEND_DIR / "static"

# Server
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8000
HEALTH_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}/api/health"
APP_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"

# Window
WINDOW_TITLE = "Heidalv Alpha Arena"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800
WINDOW_MIN_WIDTH = 1024
WINDOW_MIN_HEIGHT = 600

# Startup
HEALTH_CHECK_INTERVAL = 0.5   # seconds between health checks
HEALTH_CHECK_TIMEOUT = 30     # max seconds to wait for backend
