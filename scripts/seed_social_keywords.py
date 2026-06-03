#!/usr/bin/env python3
"""Sosyal medya anahtar kelimelerini keywords tablosuna ekler (mevcutlar atlanir)."""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "r10_tracker.db"

KEYWORDS = [
    "instagram", "insta", "ınstagram", "ig hesap", "reels", "reel",
    "tiktok", "tik tok", "tiktok hesap",
    "youtube", "youtube kanal", "yt kanal", "shorts",
    "twitter", "x hesap", "twitter hesap",
    "facebook", "fb sayfa", "fb hesap", "meta hesap",
    "telegram", "telegram kanal", "telegram grup", "tg kanal", "tg grup",
    "discord", "discord sunucu", "discord hesap",
    "snapchat", "snap hesap",
    "pinterest", "pinterest hesap",
    "linkedin", "linkedin hesap",
    "twitch", "twitch kanal",
    "threads", "threads hesap",
    "kwai", "onlyfans", "of hesap",
    "spotify", "netflix hesap",
    "hesap al", "hesap sat", "hesap kirala", "hesap devir", "hesap devredilir",
    "hesap alinir", "hesap alinacak", "hesap satilik", "hesap satılacak",
    "bos hesap", "boş hesap", "eski hesap", "hazir hesap", "hazır hesap",
    "dogrulanmis", "doğrulanmış", "onayli hesap", "onaylı hesap", "mavi tik",
    "takipci", "takipçi", "begeni", "beğeni", "izlenme", "abone", "subscriber",
    "kanal al", "kanal sat", "sayfa al", "sayfa sat", "profil sat",
    "sosyal medya", "sosyal medya hesap", "smm panel", "panel hesap",
    "influencer", "fenomen", "icerik uretici", "içerik üretici",
    "monetizasyon", "reklam hesabi", "reklam hesabı",
    "boost", "bot takipci", "bot takipçi",
    "engagement", "etkilesim", "etkileşim",
    "dm sat", "dm al", "eposta ile", "mail degis", "mail değiş",
    "kullanici adi", "kullanıcı adı", "username",
    "hesap kurtarma", "hesap acma", "hesap açma",
    "shadowban", "shadow ban", "askiya", "askıya",
    "premium hesap", "verified", "verification",
]


def main():
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    if not db_path.is_file():
        print(f"HATA: Veritabani yok: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    added = skipped = 0

    for word in KEYWORDS:
        word = word.strip()
        if not word:
            continue
        try:
            cur.execute(
                "INSERT INTO keywords (keyword, created_at) VALUES (?, ?)",
                (word, now),
            )
            added += 1
        except sqlite3.IntegrityError:
            skipped += 1

    conn.commit()
    total = cur.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]
    conn.close()

    print(f"DB: {db_path}")
    print(f"Eklenen: {added} | Zaten vardi: {skipped} | Toplam kelime: {total}")


if __name__ == "__main__":
    main()
