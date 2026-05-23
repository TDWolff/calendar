import os
from datetime import datetime, date, time, timedelta
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, text, func
import secrets


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_DIR = '/volumes' if os.path.isdir('/volumes') else os.path.join(HERE, 'data')
DB_DIR = os.environ.get('DB_DIR', DEFAULT_DB_DIR)
try:
    os.makedirs(DB_DIR, exist_ok=True)
except (OSError, PermissionError):
    DB_DIR = os.path.join(HERE, 'data')
    os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, 'calendar.db')

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JSON_SORT_KEYS'] = False

db = SQLAlchemy(app)


VALID_STATUSES = {'session', 'busy', 'tentative', 'available'}
# Statuses that count as "skipping out" on a scheduled session and therefore
# require a reasoning the rest of the party can vote on.
CONFLICTING_STATUSES = {'busy', 'tentative'}


class Event(db.Model):
    __tablename__ = 'events'

    id = db.Column(db.Integer, primary_key=True)
    # `date` is the START date of the event. `end_date` is NULL for single-day.
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
    creator_token = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def effective_end_date(self):
        return self.end_date or self.date

    def to_dict(self, voter_name=None):
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
    value = db.Column(db.Integer, nullable=False)  # +1 or -1
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    event = db.relationship(
        'Event',
        backref=db.backref('votes', cascade='all, delete-orphan', passive_deletes=True, lazy='joined'),
    )

    __table_args__ = (
        UniqueConstraint('event_id', 'voter_name', name='_event_voter_uc'),
    )


def _compute_dt_range(start_date_obj, end_date_obj, all_day, start_time_obj, end_time_obj):
    """Return [start_dt, end_dt) — the half-open datetime interval an event occupies.

    All-day events span midnight to midnight of the day after `end_date`. Timed
    events span exactly from start to end on their dates.
    """
    if end_date_obj is None:
        end_date_obj = start_date_obj
    if all_day:
        start_dt = datetime.combine(start_date_obj, datetime.min.time())
        end_dt = datetime.combine(end_date_obj + timedelta(days=1), datetime.min.time())
    else:
        start_dt = datetime.combine(start_date_obj, start_time_obj or datetime.min.time())
        end_dt = datetime.combine(end_date_obj, end_time_obj or datetime.min.time())
    return start_dt, end_dt


def _event_dt_range(evt):
    return _compute_dt_range(evt.date, evt.end_date, evt.all_day, evt.start_time, evt.end_time)


def _has_session_overlapping_dt(start_dt, end_dt, exclude_id=None):
    """True iff any session event's datetime interval overlaps [start_dt, end_dt)."""
    # Narrow candidates by date range (cheap, loose), then verify precisely.
    q = Event.query.filter(
        Event.status == 'session',
        Event.date <= end_dt.date(),
        func.coalesce(Event.end_date, Event.date) >= start_dt.date(),
    )
    if exclude_id is not None:
        q = q.filter(Event.id != exclude_id)
    for s in q.all():
        s_start, s_end = _event_dt_range(s)
        if start_dt < s_end and s_start < end_dt:
            return True
    return False


def _migrate_schema():
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
            if altered:
                conn.commit()
        except Exception as e:
            print(f"[migrate] warning: {e}")


with app.app_context():
    db.create_all()
    _migrate_schema()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/events', methods=['GET'])
def list_events():
    voter_name = (request.args.get('voter') or '').strip() or None
    start = request.args.get('start')
    end = request.args.get('end')
    q = Event.query
    if start:
        try:
            q = q.filter(Event.date >= date.fromisoformat(start[:10]))
        except ValueError:
            pass
    if end:
        try:
            q = q.filter(Event.date <= date.fromisoformat(end[:10]))
        except ValueError:
            pass
    events = q.order_by(Event.date.asc(), Event.created_at.asc()).all()
    return jsonify([e.to_dict(voter_name=voter_name) for e in events])


class _ValidationError(Exception):
    def __init__(self, message, code=None):
        self.message = message
        self.code = code


def _parse_date(raw, field_label):
    if not raw:
        raise _ValidationError(f'{field_label} is required.')
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise _ValidationError(f'{field_label} must be in YYYY-MM-DD format.')


def _parse_time(raw, field_label):
    if not raw:
        raise _ValidationError(f'{field_label} is required for timed events.')
    try:
        return time.fromisoformat(raw)
    except ValueError:
        raise _ValidationError(f'{field_label} must be in HH:MM format.')


def _parse_event_payload(data, *, partial=False):
    """Parse + validate the schedule-related fields. Returns a dict of parsed values.

    When `partial=True` (used by PATCH), only fields present in `data` are validated;
    missing fields are returned as the sentinel `None` and the caller decides whether
    to apply them.
    """
    out = {}

    if not partial or 'name' in data:
        name = (data.get('name') or '').strip()
        if not name:
            raise _ValidationError('Thy name is required.')
        if len(name) > 80:
            raise _ValidationError('Name is too long (max 80 characters).')
        out['name'] = name

    if not partial or 'title' in data:
        title = (data.get('title') or '').strip()
        if not title:
            raise _ValidationError('Pray, what befalls this day?')
        if len(title) > 200:
            raise _ValidationError('Title is too long (max 200 characters).')
        out['title'] = title

    if not partial or 'description' in data:
        description = (data.get('description') or '').strip()
        if len(description) > 2000:
            raise _ValidationError('Description too long (max 2000 characters).')
        out['description'] = description

    if not partial or 'reasoning' in data:
        reasoning = (data.get('reasoning') or '').strip()
        if len(reasoning) > 2000:
            raise _ValidationError('Reasoning too long (max 2000 characters).')
        out['reasoning'] = reasoning

    if not partial or 'status' in data:
        status = (data.get('status') or 'busy').strip().lower()
        if status not in VALID_STATUSES:
            raise _ValidationError(
                f'Status must be one of: {", ".join(sorted(VALID_STATUSES))}.'
            )
        out['status'] = status

    # Schedule fields — start_date, end_date, all_day, start_time, end_time
    # are validated together because they constrain each other.
    schedule_keys = {'start_date', 'end_date', 'all_day', 'start_time', 'end_time'}
    if not partial or schedule_keys.intersection(data.keys()):
        start_date = _parse_date(data.get('start_date'), 'Start date')
        end_raw = (data.get('end_date') or '').strip()
        end_date = _parse_date(end_raw, 'End date') if end_raw else start_date
        if end_date < start_date:
            raise _ValidationError('End date must not precede the start date.')

        all_day = data.get('all_day', True)
        if isinstance(all_day, str):
            all_day = all_day.lower() not in ('false', '0', 'no', '')
        all_day = bool(all_day)

        start_time_val = None
        end_time_val = None
        if not all_day:
            start_time_val = _parse_time(data.get('start_time'), 'Start time')
            end_time_val = _parse_time(data.get('end_time'), 'End time')
            start_dt = datetime.combine(start_date, start_time_val)
            end_dt = datetime.combine(end_date, end_time_val)
            if end_dt <= start_dt:
                raise _ValidationError('The end must come after the start.')

        out['date'] = start_date
        out['end_date'] = end_date if end_date != start_date else None
        out['all_day'] = all_day
        out['start_time'] = start_time_val
        out['end_time'] = end_time_val

    return out


@app.route('/api/events', methods=['POST'])
def create_event():
    data = request.get_json(silent=True) or {}
    creator_token = (data.get('creator_token') or '').strip()
    if not creator_token:
        return jsonify({'error': 'Creator token is required.'}), 400

    try:
        parsed = _parse_event_payload(data, partial=False)
    except _ValidationError as ve:
        return jsonify({'error': ve.message, **({'code': ve.code} if ve.code else {})}), 400

    # Session-conflict reasoning check: if the entry is busy/tentative and any
    # session overlaps the entry's date range, a reasoning is required.
    if parsed['status'] in CONFLICTING_STATUSES:
        start_dt, end_dt = _compute_dt_range(parsed['date'], parsed['end_date'], parsed['all_day'], parsed['start_time'], parsed['end_time'])
        if _has_session_overlapping_dt(start_dt, end_dt):
            if not (parsed.get('reasoning') or '').strip():
                return jsonify({
                    'error': 'A session is set for this day — thou must provide a reasoning.',
                    'code': 'reasoning_required',
                }), 400

    evt = Event(**parsed, creator_token=creator_token)
    db.session.add(evt)
    db.session.commit()
    return jsonify(evt.to_dict()), 201


@app.route('/api/events/<int:event_id>', methods=['PATCH'])
def update_event(event_id):
    evt = db.session.get(Event, event_id)
    if not evt:
        return jsonify({'error': 'Entry not found.'}), 404

    data = request.get_json(silent=True) or {}
    creator_token = (data.get('creator_token') or '').strip()
    
    # Verify the token matches (reject if no token provided or mismatch)
    if not creator_token or creator_token != evt.creator_token:
        return jsonify({'error': 'Thou art not the creator of this entry.'}), 403

    try:
        parsed = _parse_event_payload(data, partial=True)
    except _ValidationError as ve:
        return jsonify({'error': ve.message, **({'code': ve.code} if ve.code else {})}), 400

    # Re-validate session overlap when status or schedule fields change
    if any(field in parsed for field in {'status', 'date', 'end_date', 'all_day', 'start_time', 'end_time'}):
        new_status = parsed.get('status', evt.status)
        new_date = parsed.get('date', evt.date)
        new_end_date = parsed.get('end_date', evt.end_date)
        new_all_day = parsed.get('all_day', evt.all_day)
        new_start_time = parsed.get('start_time', evt.start_time)
        new_end_time = parsed.get('end_time', evt.end_time)
        
        if new_status in CONFLICTING_STATUSES:
            start_dt, end_dt = _compute_dt_range(new_date, new_end_date, new_all_day, new_start_time, new_end_time)
            if _has_session_overlapping_dt(start_dt, end_dt, exclude_id=evt.id):
                existing_reasoning = parsed.get('reasoning', evt.reasoning or '')
                if not existing_reasoning.strip():
                    return jsonify({
                        'error': 'A session is set for this day — thou must provide a reasoning.',
                        'code': 'reasoning_required',
                    }), 400

    for field, value in parsed.items():
        setattr(evt, field, value)

    db.session.commit()
    return jsonify(evt.to_dict())


@app.route('/api/events/<int:event_id>', methods=['DELETE'])
def delete_event(event_id):
    evt = db.session.get(Event, event_id)
    if not evt:
        return jsonify({'error': 'Entry not found.'}), 404
    
    data = request.get_json(silent=True) or {}
    creator_token = (data.get('creator_token') or '').strip()
    
    # Verify the token matches (reject if no token provided or mismatch)
    if not creator_token or creator_token != evt.creator_token:
        return jsonify({'error': 'Thou art not the creator of this entry.'}), 403
    
    db.session.delete(evt)
    db.session.commit()
    return '', 204


@app.route('/api/events/<int:event_id>/vote', methods=['POST'])
def vote_on_event(event_id):
    """Cast (or change, or clear) a vote on an event's reasoning.

    Body: { voter_name: str, value: -1 | 0 | 1 }
        value=1  → upvote
        value=-1 → downvote
        value=0  → clear vote
    """
    evt = db.session.get(Event, event_id)
    if not evt:
        return jsonify({'error': 'Entry not found.'}), 404

    data = request.get_json(silent=True) or {}
    voter_name = (data.get('voter_name') or '').strip()
    try:
        value = int(data.get('value', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Vote value must be -1, 0, or 1.'}), 400

    if not voter_name:
        return jsonify({'error': 'Voter name is required.'}), 400
    if len(voter_name) > 80:
        return jsonify({'error': 'Voter name is too long.'}), 400
    if value not in (-1, 0, 1):
        return jsonify({'error': 'Vote value must be -1, 0, or 1.'}), 400

    # Only conflicting entries (busy / tentative) overlapping a session are vote-worthy.
    start_dt, end_dt = _event_dt_range(evt)
    if evt.status not in CONFLICTING_STATUSES or not _has_session_overlapping_dt(
        start_dt, end_dt, exclude_id=evt.id
    ):
        return jsonify({'error': 'This entry is not open for voting.'}), 400

    existing = Vote.query.filter(
        Vote.event_id == event_id,
        func.lower(Vote.voter_name) == voter_name.lower(),
    ).first()

    if value == 0:
        if existing:
            db.session.delete(existing)
    else:
        if existing:
            existing.value = value
            existing.voter_name = voter_name  # keep latest casing
        else:
            db.session.add(Vote(event_id=event_id, voter_name=voter_name, value=value))

    db.session.commit()
    db.session.refresh(evt)
    return jsonify(evt.to_dict(voter_name=voter_name))


@app.route('/healthz')
def healthz():
    return {'ok': True}


@app.route('/api/session-token', methods=['GET'])
def get_session_token():
    """Return a session token for this client. Tokens persist via localStorage."""
    token = secrets.token_hex(32)  # 64-character hex string
    return jsonify({'token': token})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8093, debug=True)
