from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from sqlalchemy import UniqueConstraint, text
from werkzeug.security import generate_password_hash, check_password_hash


db = SQLAlchemy()


VALID_STATUSES = {'session', 'busy', 'tentative', 'available'}
CONFLICTING_STATUSES = {'busy', 'tentative'}


class User(db.Model, UserMixin):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)


class Event(db.Model):
    __tablename__ = 'events'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    end_date = db.Column(db.Date, nullable=True)
    all_day = db.Column(db.Boolean, nullable=False, default=True)
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    name = db.Column(db.String(80), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    reasoning = db.Column(db.Text, default='')
    status = db.Column(db.String(20), nullable=False, default='busy')
    creator_token = db.Column(db.String(64), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('events', lazy='dynamic'))

    @property
    def effective_end_date(self):
        return self.end_date or self.date

    def to_dict(self, voter_name=None, current_user_id=None):
        votes = list(self.votes) if self.votes is not None else []
        up = sum(1 for v in votes if v.value == 1)
        down = sum(1 for v in votes if v.value == -1)
        my_vote = None
        if voter_name:
            nn = voter_name.strip().lower()
            for v in votes:
                if (v.voter_name or '').lower() == nn:
                    my_vote = v.value
                    break
        return {
            'id': self.id,
            'start_date': self.date.isoformat(),
            'end_date': self.effective_end_date.isoformat(),
            'all_day': bool(self.all_day),
            'start_time': self.start_time.isoformat(timespec='minutes') if self.start_time else None,
            'end_time': self.end_time.isoformat(timespec='minutes') if self.end_time else None,
            'name': self.name,
            'title': self.title,
            'description': self.description or '',
            'reasoning': self.reasoning or '',
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'owned_by_me': current_user_id is not None and self.user_id == current_user_id,
            'votes': {
                'up': up,
                'down': down,
                'net': up - down,
                'my_vote': my_vote,
            },
        }


class Vote(db.Model):
    __tablename__ = 'votes'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(
        db.Integer,
        db.ForeignKey('events.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    voter_name = db.Column(db.String(80), nullable=False)
    value = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    event = db.relationship(
        'Event',
        backref=db.backref('votes', cascade='all, delete-orphan', passive_deletes=True, lazy='joined'),
    )

    __table_args__ = (
        UniqueConstraint('event_id', 'voter_name', name='_event_voter_uc'),
    )


def migrate_schema():
    """Lightweight inline schema migration so existing SQLite DBs pick up new columns."""
    with db.engine.connect() as conn:
        try:
            result = conn.execute(text("PRAGMA table_info(events)"))
            existing_cols = {row[1] for row in result}
            altered = False
            if 'reasoning' not in existing_cols:
                conn.execute(text("ALTER TABLE events ADD COLUMN reasoning TEXT DEFAULT ''"))
                altered = True
            if 'end_date' not in existing_cols:
                conn.execute(text("ALTER TABLE events ADD COLUMN end_date DATE"))
                altered = True
            if 'all_day' not in existing_cols:
                conn.execute(text("ALTER TABLE events ADD COLUMN all_day BOOLEAN NOT NULL DEFAULT 1"))
                altered = True
            if 'start_time' not in existing_cols:
                conn.execute(text("ALTER TABLE events ADD COLUMN start_time TIME"))
                altered = True
            if 'end_time' not in existing_cols:
                conn.execute(text("ALTER TABLE events ADD COLUMN end_time TIME"))
                altered = True
            if 'creator_token' not in existing_cols:
                conn.execute(text("ALTER TABLE events ADD COLUMN creator_token VARCHAR(64)"))
                altered = True
            if 'user_id' not in existing_cols:
                conn.execute(text("ALTER TABLE events ADD COLUMN user_id INTEGER REFERENCES users(id)"))
                altered = True
            if altered:
                conn.commit()
        except Exception as e:
            print(f"[migrate] warning: {e}")
