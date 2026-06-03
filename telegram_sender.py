import html
import logging

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LENGTH = 4096
MATCHES_PER_MESSAGE = 5


def _escape(text: str) -> str:
    return html.escape(text or "")


def _keyword_display(match: dict) -> str:
    kws = match.get("keywords")
    if kws:
        return ", ".join(kws)
    return match.get("keyword") or ""


def group_matches_for_notification(matches: list[dict]) -> list[dict]:
    """Merge rows that refer to the same topic (by URL) into one notification line."""
    by_url: dict[str, dict] = {}
    for m in matches:
        url = m.get("url") or ""
        if not url:
            continue
        if url not in by_url:
            kws = list(m.get("keywords") or [])
            if not kws and m.get("keyword"):
                kws = [m["keyword"]]
            by_url[url] = {
                "title": m["title"],
                "url": url,
                "category_name": m["category_name"],
                "keywords": list(dict.fromkeys(kws)),
                "matched_ids": list(m.get("matched_ids") or []),
            }
            if m.get("matched_id"):
                by_url[url]["matched_ids"].append(m["matched_id"])
        else:
            entry = by_url[url]
            for kw in m.get("keywords") or []:
                if kw not in entry["keywords"]:
                    entry["keywords"].append(kw)
            if m.get("keyword") and m["keyword"] not in entry["keywords"]:
                entry["keywords"].append(m["keyword"])
            for mid in m.get("matched_ids") or []:
                if mid not in entry["matched_ids"]:
                    entry["matched_ids"].append(mid)
            if m.get("matched_id") and m["matched_id"] not in entry["matched_ids"]:
                entry["matched_ids"].append(m["matched_id"])

    grouped = []
    for entry in by_url.values():
        entry["keyword"] = ", ".join(entry["keywords"])
        grouped.append(entry)
    return grouped


def _format_matches_chunk(matches: list[dict], part: int, total_parts: int) -> str:
    header = f"<b>R10 Takip</b>: {len(matches)} yeni eşleşme"
    if total_parts > 1:
        header += f" ({part}/{total_parts})"

    lines = [header, ""]
    for m in matches:
        title = _escape(m["title"])
        url = m["url"]
        category = _escape(m["category_name"])
        keywords = _escape(_keyword_display(m))
        safe_url = _escape(url)
        lines.append(f'• <a href="{safe_url}">{title}</a>')
        lines.append(f"  Kategori: {category} | Kelime: <b>{keywords}</b>")
        lines.append("")

    return "\n".join(lines).strip()


def _chunk_matches(matches: list[dict]) -> list[str]:
    """Split matches into Telegram-sized message bodies."""
    if not matches:
        return []

    grouped = group_matches_for_notification(matches)
    chunks: list[str] = []
    for i in range(0, len(grouped), MATCHES_PER_MESSAGE):
        batch = grouped[i : i + MATCHES_PER_MESSAGE]
        total_parts = (len(grouped) + MATCHES_PER_MESSAGE - 1) // MATCHES_PER_MESSAGE
        part = i // MATCHES_PER_MESSAGE + 1
        text = _format_matches_chunk(batch, part, total_parts)
        while len(text) > MAX_MESSAGE_LENGTH and len(batch) > 1:
            mid = len(batch) // 2
            left = _format_matches_chunk(batch[:mid], part, total_parts)
            right = _format_matches_chunk(batch[mid:], part, total_parts)
            chunks.extend([left, right])
            break
        else:
            chunks.append(text)

    return chunks


def send_telegram_message(
    bot_token: str, chat_id: str, text: str, *, parse_mode: str | None = "HTML"
) -> tuple[bool, str]:
    """Send a single Telegram message. Returns (success, error_message)."""
    if not bot_token or not chat_id:
        return False, "Token veya chat_id eksik"

    api_url = TELEGRAM_API.format(token=bot_token.strip())
    payload: dict = {
        "chat_id": chat_id.strip(),
        "text": text,
        "disable_web_page_preview": False,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        resp = requests.post(api_url, json=payload, timeout=15)
        data = resp.json()
        if not data.get("ok"):
            err = data.get("description", resp.text)
            logger.error("Telegram API hatasi: %s", err)
            return False, str(err)
        return True, ""
    except requests.RequestException as exc:
        logger.error("Telegram istegi basarisiz: %s", exc)
        return False, str(exc)


def _plain_text_notification(matches: list[dict]) -> str:
    grouped = group_matches_for_notification(matches)
    lines = [f"R10 Takip: {len(grouped)} yeni eşleşme", ""]
    for m in grouped:
        lines.append(f"• {m['title']}")
        lines.append(f"  {m['url']}")
        lines.append(f"  {m['category_name']} | {_keyword_display(m)}")
        lines.append("")
    return "\n".join(lines).strip()


def send_telegram_notification(
    bot_token: str, chat_id: str, matches: list[dict]
) -> tuple[bool, str]:
    """Send notification(s). Returns (success, error_message)."""
    if not matches:
        return True, ""

    grouped = group_matches_for_notification(matches)
    bodies = _chunk_matches(matches)
    if not bodies:
        return False, "Mesaj olusturulamadi"

    for body in bodies:
        ok, err = send_telegram_message(bot_token, chat_id, body, parse_mode="HTML")
        if not ok:
            logger.warning("HTML Telegram basarisiz, duz metin deneniyor: %s", err)
            plain = _plain_text_notification(matches)
            ok, err = send_telegram_message(
                bot_token, chat_id, plain, parse_mode=None
            )
        if not ok:
            return False, err or "Telegram gonderilemedi"

    logger.info("Telegram bildirimi gonderildi (%d konu).", len(grouped))
    return True, ""


def send_test_message(bot_token: str, chat_id: str) -> bool:
    text = (
        "<b>R10 Konu Takip</b>\n\n"
        "Test mesajı başarılı. Yeni konu eşleşmelerinde buraya bildirim gelecek."
    )
    ok, _ = send_telegram_message(bot_token, chat_id, text)
    return ok


def send_single_match(bot_token: str, chat_id: str, match: dict) -> tuple[bool, str]:
    """Resend one matched topic to Telegram."""
    return send_telegram_notification(bot_token, chat_id, [match])
