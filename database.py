from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    r10_forum_id = db.Column(db.Integer, unique=True, nullable=False)
    name = db.Column(db.String(256), nullable=False)
    url = db.Column(db.String(512), nullable=False)
    parent_name = db.Column(db.String(256), nullable=True)
    is_active = db.Column(db.Boolean, default=False)

    seen_topics = db.relationship("SeenTopic", backref="category", lazy=True)

    def __repr__(self):
        return f"<Category {self.name} (f-{self.r10_forum_id})>"


class Keyword(db.Model):
    __tablename__ = "keywords"

    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(256), unique=True, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    matched_topics = db.relationship("MatchedTopic", backref="keyword", lazy=True)

    def __repr__(self):
        return f"<Keyword {self.keyword}>"


class SeenTopic(db.Model):
    __tablename__ = "seen_topics"

    id = db.Column(db.Integer, primary_key=True)
    r10_topic_id = db.Column(db.Integer, unique=True, nullable=False, index=True)
    title = db.Column(db.String(512), nullable=False)
    url = db.Column(db.String(512), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    first_seen_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    matched = db.relationship("MatchedTopic", backref="topic", lazy=True)

    def __repr__(self):
        return f"<SeenTopic {self.title[:40]}>"


class MatchedTopic(db.Model):
    __tablename__ = "matched_topics"

    id = db.Column(db.Integer, primary_key=True)
    topic_id = db.Column(db.Integer, db.ForeignKey("seen_topics.id"), nullable=False)
    keyword_id = db.Column(db.Integer, db.ForeignKey("keywords.id"), nullable=False)
    notified_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    telegram_sent = db.Column(db.Boolean, default=False)
    telegram_error = db.Column(db.String(512), nullable=True)

    def __repr__(self):
        return f"<MatchedTopic topic={self.topic_id} kw={self.keyword_id}>"


class Setting(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(128), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<Setting {self.key}>"


SETTING_DEFAULTS = {
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "scanner_active": "0",
    "setup_complete": "0",
    "admin_username": "",
    "admin_password_hash": "",
}


def get_setting(key: str) -> str:
    row = Setting.query.filter_by(key=key).first()
    if row:
        return row.value
    return SETTING_DEFAULTS.get(key, "")


def set_setting(key: str, value: str):
    row = Setting.query.filter_by(key=key).first()
    if row:
        row.value = value
    else:
        row = Setting(key=key, value=value)
        db.session.add(row)
    db.session.commit()


def init_default_settings():
    for key, default_value in SETTING_DEFAULTS.items():
        existing = Setting.query.filter_by(key=key).first()
        if not existing:
            db.session.add(Setting(key=key, value=default_value))
    db.session.commit()


def migrate_db():
    """Add new columns to existing SQLite DB without losing data."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    if "matched_topics" not in inspector.get_table_names():
        return

    cols = {c["name"] for c in inspector.get_columns("matched_topics")}
    statements = []
    if "telegram_sent" not in cols:
        statements.append(
            "ALTER TABLE matched_topics ADD COLUMN telegram_sent BOOLEAN DEFAULT 0"
        )
    if "telegram_error" not in cols:
        statements.append(
            "ALTER TABLE matched_topics ADD COLUMN telegram_error VARCHAR(512)"
        )

    if not statements:
        return

    with db.engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
