import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from apscheduler.schedulers.background import BackgroundScheduler

from config import Config
from database import (
    db,
    Category,
    Keyword,
    SeenTopic,
    MatchedTopic,
    get_setting,
)
from scraper import fetch_topics, matches_keyword
from telegram_sender import group_matches_for_notification, send_telegram_notification

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(daemon=True)
_app = None
_scan_lock = threading.Lock()

JOB_ID = "r10_scan"
BASELINE_JOB_ID = "r10_baseline"


def _fetch_category_page(cat: Category, stagger_index: int):
    """HTTP-only fetch for parallel workers (no DB)."""
    time.sleep(stagger_index * Config.SCAN_STAGGER_SECONDS)
    topics = fetch_topics(cat.r10_forum_id, page=1)
    return cat, topics


def _fetch_all_categories_parallel(active_categories: list[Category]):
    """Fetch page 1 for all active categories with limited concurrency."""
    if not active_categories:
        return []

    results: list[tuple[Category, list]] = []
    workers = min(Config.SCAN_MAX_WORKERS, len(active_categories))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_fetch_category_page, cat, idx): cat
            for idx, cat in enumerate(active_categories)
        }
        for future in as_completed(futures):
            cat = futures[future]
            try:
                _, topics = future.result()
                results.append((cat, topics))
                logger.info(
                    "Kategori tarandi: %s (f-%s), %d konu",
                    cat.name,
                    cat.r10_forum_id,
                    len(topics),
                )
            except Exception as exc:
                logger.error(
                    "Kategori tarama hatasi %s (f-%s): %s",
                    cat.name,
                    cat.r10_forum_id,
                    exc,
                )
    return results


def _matching_keywords(title: str, keyword_list: list[str]) -> list[str]:
    return [kw for kw in keyword_list if matches_keyword(title, kw)]


def _process_topic(
    cat: Category,
    topic,
    *,
    baseline: bool,
    keyword_list: list[str],
    keyword_map: dict,
) -> tuple[int, int]:
    """Record seen topic; on match send Telegram immediately (grouped keywords)."""
    existing = SeenTopic.query.filter_by(r10_topic_id=topic.topic_id).first()
    if existing:
        return 0, 0

    seen = SeenTopic(
        r10_topic_id=topic.topic_id,
        title=topic.title,
        url=topic.url,
        category_id=cat.id,
    )
    db.session.add(seen)
    db.session.flush()
    new_seen = 1
    match_count = 0

    if baseline:
        db.session.commit()
        return new_seen, match_count

    matched_kws = _matching_keywords(topic.title, keyword_list)
    if not matched_kws:
        db.session.commit()
        return new_seen, match_count

    matched_rows: list[MatchedTopic] = []
    for kw_text in matched_kws:
        matched = MatchedTopic(
            topic_id=seen.id,
            keyword_id=keyword_map[kw_text].id,
        )
        db.session.add(matched)
        matched_rows.append(matched)

    db.session.flush()
    match_count = len(matched_rows)
    logger.info(
        "ESLESME: '%s' -> kelimeler: %s",
        topic.title,
        ", ".join(matched_kws),
    )

    notification = {
        "title": topic.title,
        "url": topic.url,
        "category_name": cat.name,
        "keywords": matched_kws,
        "keyword": ", ".join(matched_kws),
        "matched_ids": [m.id for m in matched_rows],
    }
    db.session.commit()
    _deliver_telegram_for_matches([notification])
    return new_seen, match_count


def _process_category_results(
    cat: Category,
    topics: list,
    *,
    baseline: bool,
    keyword_list: list[str],
    keyword_map: dict,
) -> tuple[int, int]:
    new_seen = 0
    match_count = 0
    for topic in topics:
        ns, mc = _process_topic(
            cat,
            topic,
            baseline=baseline,
            keyword_list=keyword_list,
            keyword_map=keyword_map,
        )
        new_seen += ns
        match_count += mc
    if baseline:
        db.session.commit()
    return new_seen, match_count


def _run_scan(*, baseline: bool = False):
    """Parallel page-1 scan; instant Telegram per new matching topic."""
    if _app is None:
        return

    with _app.app_context():
        with _scan_lock:
            _run_scan_locked(baseline=baseline)


def _run_scan_locked(*, baseline: bool = False):
    active_categories = Category.query.filter_by(is_active=True).all()

    if not active_categories:
        logger.info("Tarama atlandi: aktif kategori yok.")
        return

    keywords = Keyword.query.all()
    if not baseline and not keywords:
        logger.info("Tarama atlandi: anahtar kelime yok.")
        return

    keyword_list = [kw.keyword for kw in keywords]
    keyword_map = {kw.keyword: kw for kw in keywords}
    new_seen = 0
    match_count = 0

    mode = "baseline" if baseline else "normal"
    logger.info(
        "Tarama basladi (%s modu, %d kategori, paralel).",
        mode,
        len(active_categories),
    )

    fetched = _fetch_all_categories_parallel(active_categories)

    for cat, topics in fetched:
        ns, mc = _process_category_results(
            cat,
            topics,
            baseline=baseline,
            keyword_list=keyword_list,
            keyword_map=keyword_map,
        )
        new_seen += ns
        match_count += mc

    if baseline:
        logger.info(
            "Baseline tamamlandi. %d konu kaydedildi (bildirim gonderilmedi).",
            new_seen,
        )
        return

    logger.info(
        "Tarama tamamlandi. %d yeni konu, %d eslesme (log satiri).",
        new_seen,
        match_count,
    )


def _run_baseline_then_start_periodic():
    """One-shot baseline on start, then enable interval scanning."""
    _run_scan(baseline=True)
    _ensure_job_running()
    logger.info("Periyodik tarama devreye girdi.")


def _deliver_telegram_for_matches(matches: list[dict]):
    """Send Telegram for matches and persist delivery status on each row."""
    bot_token = get_setting("telegram_bot_token")
    chat_id = get_setting("telegram_chat_id")

    grouped = group_matches_for_notification(matches)

    if not bot_token or not chat_id:
        err = "Telegram ayarlari eksik"
        logger.error("%s; %d konu bildirimi gonderilemedi.", err, len(grouped))
        _mark_telegram_results(grouped, False, err)
        return

    payload = [
        {k: v for k, v in m.items() if k not in ("matched_id", "matched_ids")}
        for m in grouped
    ]
    ok, err = send_telegram_notification(
        bot_token=bot_token,
        chat_id=chat_id,
        matches=payload,
    )
    if ok:
        logger.info("Telegram bildirimi basarili: %d konu.", len(grouped))
    else:
        logger.error("Telegram bildirimi BASARISIZ: %s", err)
    _mark_telegram_results(grouped, ok, err)


def _mark_telegram_results(matches: list[dict], ok: bool, error: str):
    for m in matches:
        for mid in m.get("matched_ids") or []:
            row = MatchedTopic.query.get(mid)
            if row:
                row.telegram_sent = ok
                row.telegram_error = None if ok else (error or "Bilinmeyen hata")[:512]
        mid = m.get("matched_id")
        if mid:
            row = MatchedTopic.query.get(mid)
            if row:
                row.telegram_sent = ok
                row.telegram_error = None if ok else (error or "Bilinmeyen hata")[:512]
    db.session.commit()


def init_scheduler(app):
    """Bind to the Flask app and start the scheduler if scanning is active."""
    global _app
    _app = app

    if not _scheduler.running:
        _scheduler.start()

    if get_setting("scanner_active") == "1":
        _ensure_job_running()


def _ensure_job_running():
    seconds = Config.SCAN_INTERVAL_SECONDS
    existing = _scheduler.get_job(JOB_ID)
    if existing:
        _scheduler.reschedule_job(JOB_ID, trigger="interval", seconds=seconds)
        _scheduler.modify_job(JOB_ID, max_instances=1, coalesce=True)
    else:
        _scheduler.add_job(
            _run_scan,
            trigger="interval",
            seconds=seconds,
            id=JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    logger.info(
        "Zamanlayici aktif: her %d saniyede paralel tarama (max %d eszamanli istek).",
        seconds,
        Config.SCAN_MAX_WORKERS,
    )


def start_scanning():
    """Run baseline (no mail), then start periodic scanning."""
    existing = _scheduler.get_job(JOB_ID)
    if existing:
        _scheduler.remove_job(JOB_ID)

    _scheduler.add_job(
        _run_baseline_then_start_periodic,
        id=BASELINE_JOB_ID,
        replace_existing=True,
        max_instances=1,
    )
    logger.info("Baseline taramasi baslatildi, ardindan periyodik tarama acilacak.")


def stop_scanning():
    """Stop periodic scanning and any pending baseline job."""
    for job_id in (JOB_ID, BASELINE_JOB_ID):
        existing = _scheduler.get_job(job_id)
        if existing:
            _scheduler.remove_job(job_id)
    logger.info("Tarama durduruldu.")


def is_scanning() -> bool:
    return (
        _scheduler.get_job(JOB_ID) is not None
        or _scheduler.get_job(BASELINE_JOB_ID) is not None
    )


def is_scanner_enabled() -> bool:
    """Whether the user has turned scanning on (persisted in DB)."""
    return get_setting("scanner_active") == "1"


def sync_scanner_jobs():
    """Restore or refresh scheduler jobs when scanning is enabled."""
    if not is_scanner_enabled():
        return
    if _scheduler.get_job(JOB_ID):
        _ensure_job_running()
    elif not is_scanning():
        _ensure_job_running()
        logger.info("Tarama isleri veritabani ayarina gore yeniden kuruldu.")


def run_scan_now():
    """Trigger an immediate scan (non-blocking, runs in scheduler thread)."""
    _scheduler.add_job(
        _run_scan,
        id="r10_scan_once",
        replace_existing=True,
        max_instances=1,
    )
