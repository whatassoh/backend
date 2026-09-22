from .base import *

DEBUG = False

SECURE_SSL_REDIRECT = True

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

SECURE_PROXY_SSL_HEADER = (
    "HTTP_X_FORWARDED_PROTO",
    "https",
)

# Internal Prometheus scrape endpoint.
# Nginx blocks /internal/ from public access; Prometheus reaches it
# directly through the private Docker network over HTTP.
SECURE_REDIRECT_EXEMPT = [
    r"^internal/metrics$",
]
