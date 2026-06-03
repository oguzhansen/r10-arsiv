import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "r10-arsiv-takip-gizli-anahtar")
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, 'r10_tracker.db')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    R10_ARCHIVE_URL = "https://www.r10.net/archive/"
    R10_BASE_URL = "https://www.r10.net"
    # Paralel tarama: ~10 kategori icin ban riski dusuk, bildirim gecikmesi ~1-2 dk
    SCAN_INTERVAL_SECONDS = 60
    SCAN_MAX_WORKERS = 3
    SCAN_STAGGER_SECONDS = 0.4
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
