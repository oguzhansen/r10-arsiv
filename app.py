# -*- coding: utf-8 -*-
import logging
from collections import defaultdict
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from sqlalchemy import func

from config import Config
from database import (
    db,
    Category,
    Keyword,
    SeenTopic,
    MatchedTopic,
    get_setting,
    set_setting,
    init_default_settings,
    migrate_db,
)
from scraper import fetch_all_categories
from telegram_sender import send_test_message, send_single_match
from scheduler import (
    init_scheduler,
    start_scanning,
    stop_scanning,
    is_scanner_enabled,
    sync_scanner_jobs,
    run_scan_now,
)
from auth import (
    MIN_PASSWORD_LENGTH,
    is_setup_complete,
    create_admin,
    verify_login,
    login_user,
    logout_user,
    register_auth_hooks,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = Flask(__name__)
app.config.from_object(Config)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

db.init_app(app)

with app.app_context():
    db.create_all()
    init_default_settings()
    migrate_db()
    init_scheduler(app)

register_auth_hooks(app)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if is_setup_complete():
        return redirect(url_for("login"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        if not username:
            flash("Kullanıcı adı boş olamaz.", "warning")
            return redirect(url_for("setup"))
        if len(password) < MIN_PASSWORD_LENGTH:
            flash(f"Şifre en az {MIN_PASSWORD_LENGTH} karakter olmalı.", "warning")
            return redirect(url_for("setup"))
        if password != password2:
            flash("Şifreler eşleşmiyor.", "warning")
            return redirect(url_for("setup"))

        create_admin(username, password)
        login_user()
        flash("Kurulum tamamlandı. Panele hoş geldiniz.", "success")
        return redirect(url_for("index"))

    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not is_setup_complete():
        return redirect(url_for("setup"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if verify_login(username, password):
            login_user()
            next_url = request.form.get("next") or request.args.get("next")
            if next_url and next_url.startswith("/"):
                return redirect(next_url)
            return redirect(url_for("index"))
        flash("Kullanıcı adı veya şifre hatalı.", "danger")

    next_url = request.args.get("next", "")
    return render_template("login.html", next_url=next_url)


@app.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("Çıkış yapıldı.", "info")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    categories = Category.query.order_by(Category.parent_name, Category.name).all()

    grouped: dict[str, list] = defaultdict(list)
    for cat in categories:
        grouped[cat.parent_name or "Diğer"].append(cat)

    keywords = Keyword.query.order_by(Keyword.created_at.desc()).all()
    sync_scanner_jobs()
    scanning = is_scanner_enabled()

    return render_template(
        "index.html",
        grouped_categories=dict(grouped),
        keywords=keywords,
        scanning=scanning,
        scan_interval_seconds=Config.SCAN_INTERVAL_SECONDS,
    )


@app.route("/refresh-categories", methods=["POST"])
def refresh_categories():
    cat_infos = fetch_all_categories()
    if not cat_infos:
        flash(
            "Kategoriler çekilemedi. Sunucuda: pip install -r requirements.txt "
            "(curl_cffi gerekli). IP engelleniyorsa hosting desteğine sorun.",
            "danger",
        )
        return redirect(url_for("index"))

    existing_ids = {c.r10_forum_id for c in Category.query.all()}
    added = 0
    for ci in cat_infos:
        if ci.forum_id not in existing_ids:
            db.session.add(
                Category(
                    r10_forum_id=ci.forum_id,
                    name=ci.name,
                    url=ci.url,
                    parent_name=ci.parent_name,
                    is_active=False,
                )
            )
            added += 1

    db.session.commit()
    flash(f"Kategoriler güncellendi. {added} yeni kategori eklendi.", "success")
    return redirect(url_for("index"))


@app.route("/toggle-category/<int:cat_id>", methods=["POST"])
def toggle_category(cat_id):
    cat = Category.query.get_or_404(cat_id)
    cat.is_active = not cat.is_active
    db.session.commit()
    return jsonify({"is_active": cat.is_active})


@app.route("/toggle-category-group", methods=["POST"])
def toggle_category_group():
    data = request.get_json(silent=True) or {}
    parent = data.get("parent_name", "")
    active = data.get("active")
    if active is None:
        return jsonify({"error": "active gerekli"}), 400

    if parent == "Diğer":
        query = Category.query.filter(Category.parent_name.is_(None))
    else:
        query = Category.query.filter_by(parent_name=parent)

    cats = query.all()
    for cat in cats:
        cat.is_active = bool(active)
    db.session.commit()
    return jsonify(
        {
            "parent_name": parent,
            "active": bool(active),
            "updated": len(cats),
            "ids": [c.id for c in cats],
        }
    )


def _parse_keyword_input(raw: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for chunk in raw.replace(";", ",").split(","):
        word = chunk.strip()
        if not word or word in seen:
            continue
        seen.add(word)
        result.append(word)
    return result


@app.route("/add-keyword", methods=["POST"])
def add_keyword():
    raw = request.form.get("keyword", "").strip()
    words = _parse_keyword_input(raw)
    if not words:
        flash("Anahtar kelime boş olamaz.", "warning")
        return redirect(url_for("index"))

    added: list[str] = []
    skipped: list[str] = []
    for word in words:
        if Keyword.query.filter_by(keyword=word).first():
            skipped.append(word)
            continue
        db.session.add(Keyword(keyword=word))
        added.append(word)

    db.session.commit()

    if added and skipped:
        flash(
            f"{len(added)} kelime eklendi. Zaten vardı: {', '.join(skipped[:5])}"
            + ("..." if len(skipped) > 5 else ""),
            "success",
        )
    elif added:
        if len(added) == 1:
            flash(f"'{added[0]}' eklendi.", "success")
        else:
            flash(f"{len(added)} anahtar kelime eklendi.", "success")
    else:
        flash("Girilen kelimelerin hepsi zaten kayıtlı.", "info")

    return redirect(url_for("index"))


@app.route("/delete-keyword/<int:kw_id>", methods=["POST"])
def delete_keyword(kw_id):
    kw = Keyword.query.get_or_404(kw_id)
    MatchedTopic.query.filter_by(keyword_id=kw.id).delete()
    db.session.delete(kw)
    db.session.commit()
    flash(f"'{kw.keyword}' silindi.", "success")
    return redirect(url_for("index"))


@app.route("/delete-all-keywords", methods=["POST"])
def delete_all_keywords():
    count = Keyword.query.count()
    if count == 0:
        flash("Silinecek anahtar kelime yok.", "info")
        return redirect(url_for("index"))

    MatchedTopic.query.delete()
    Keyword.query.delete()
    db.session.commit()
    flash(f"Tüm anahtar kelimeler silindi ({count} adet).", "success")
    return redirect(url_for("index"))


@app.route("/scanner/start", methods=["POST"])
def scanner_start():
    if is_scanner_enabled():
        sync_scanner_jobs()
        flash("Tarama zaten aktif. Değişiklik için önce durdurup tekrar başlatın.", "info")
        return redirect(url_for("index"))

    set_setting("scanner_active", "1")
    start_scanning()
    flash(
        "Tarama başlatıldı. Önce mevcut konular sessizce kaydedilecek (baseline), "
        "ardından seçili kategoriler paralel taranıp yeni eşleşmelerde anında Telegram gidecek.",
        "success",
    )
    return redirect(url_for("index"))


@app.route("/scanner/stop", methods=["POST"])
def scanner_stop():
    set_setting("scanner_active", "0")
    stop_scanning()
    flash("Tarama durduruldu.", "info")
    return redirect(url_for("index"))


@app.route("/scanner/run-now", methods=["POST"])
def scanner_run_now():
    if not is_scanner_enabled():
        flash("Önce Dashboard'dan taramayı başlatın.", "warning")
        return redirect(request.referrer or url_for("index"))

    sync_scanner_jobs()
    run_scan_now()
    flash("Tarama tetiklendi, arka planda çalışacak.", "info")
    return redirect(request.referrer or url_for("index"))


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        for key in ("telegram_bot_token", "telegram_chat_id"):
            set_setting(key, request.form.get(key, "").strip())
        flash("Ayarlar kaydedildi.", "success")
        return redirect(url_for("settings"))

    current = {
        key: get_setting(key)
        for key in ("telegram_bot_token", "telegram_chat_id")
    }
    return render_template("settings.html", settings=current)


@app.route("/settings/test-telegram", methods=["POST"])
def test_telegram():
    token = get_setting("telegram_bot_token")
    chat_id = get_setting("telegram_chat_id")
    if not token or not chat_id:
        flash("Önce bot token ve chat ID kaydedin.", "warning")
        return redirect(url_for("settings"))

    if send_test_message(token, chat_id):
        flash("Test mesajı Telegram'a gönderildi.", "success")
    else:
        flash(
            "Telegram mesajı gönderilemedi. Token, chat ID ve bota /start yazdığınızı kontrol edin.",
            "danger",
        )
    return redirect(url_for("settings"))


def _to_istanbul(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("Europe/Istanbul"))


@app.route("/logs")
def logs():
    page = request.args.get("page", 1, type=int)
    per_page = 50

    query = (
        db.session.query(
            SeenTopic,
            Category,
            func.max(MatchedTopic.notified_at).label("last_notified"),
            func.max(MatchedTopic.id).label("latest_match_id"),
            func.max(MatchedTopic.telegram_sent).label("any_sent"),
        )
        .join(MatchedTopic, MatchedTopic.topic_id == SeenTopic.id)
        .join(Category, SeenTopic.category_id == Category.id)
        .group_by(SeenTopic.id)
        .order_by(func.max(MatchedTopic.notified_at).desc())
    )

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    entries = []
    for topic, category, last_notified, latest_match_id, any_sent in pagination.items:
        telegram_error = None
        if not any_sent:
            failed = (
                MatchedTopic.query.filter_by(topic_id=topic.id, telegram_sent=False)
                .filter(MatchedTopic.telegram_error.isnot(None))
                .first()
            )
            if failed:
                telegram_error = failed.telegram_error

        entries.append(
            {
                "id": latest_match_id,
                "title": topic.title,
                "url": topic.url,
                "category": category.name,
                "date": _to_istanbul(last_notified),
                "telegram_sent": bool(any_sent),
                "telegram_error": telegram_error,
            }
        )

    sync_scanner_jobs()
    scanning = is_scanner_enabled()
    unique_matched = (
        db.session.query(func.count(func.distinct(MatchedTopic.topic_id))).scalar() or 0
    )
    stats = {
        "keywords": Keyword.query.count(),
        "active_categories": Category.query.filter_by(is_active=True).count(),
        "seen_topics": SeenTopic.query.count(),
        "matched_topics": unique_matched,
    }

    return render_template(
        "logs.html",
        entries=entries,
        pagination=pagination,
        scanning=scanning,
        stats=stats,
    )


@app.route("/logs/resend-telegram/<int:match_id>", methods=["POST"])
def resend_telegram(match_id):
    row = MatchedTopic.query.get_or_404(match_id)
    topic = SeenTopic.query.get_or_404(row.topic_id)
    category = Category.query.get_or_404(topic.category_id)

    token = get_setting("telegram_bot_token")
    chat_id = get_setting("telegram_chat_id")
    if not token or not chat_id:
        flash("Telegram ayarları eksik.", "warning")
        return redirect(url_for("logs"))

    match = {
        "title": topic.title,
        "url": topic.url,
        "category_name": category.name,
        "keyword": "eşleşme",
    }
    ok, err = send_single_match(token, chat_id, match)
    for m in MatchedTopic.query.filter_by(topic_id=topic.id).all():
        m.telegram_sent = ok
        m.telegram_error = None if ok else (err or "Hata")[:512]
    db.session.commit()

    if ok:
        flash("Telegram mesajı gönderildi.", "success")
    else:
        flash(f"Telegram hatası: {err}", "danger")
    return redirect(url_for("logs"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
