import secrets
from datetime import datetime, date, time, timedelta
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func

from models import db, Event, Vote, VALID_STATUSES, CONFLICTING_STATUSES


calendar_bp = Blueprint('calendar', __name__)


@calendar_bp.route('/')
@login_required
def index():
    return render_template('index.html')


# --------------------------------------------------------------------------- helpers

def _compute_dt_range(start_date_obj, end_date_obj, all_day, start_time_obj, end_time_obj):
    """Return [start_dt, end_dt) — the half-open datetime interval an event occupies."""
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


def _check_ownership(evt, provided_token):
    """Returns (allowed, should_claim).

    - allowed: whether the current user may mutate this event.
    - should_claim: whether granting access should also set user_id to the
      current user (so the event becomes theirs going forward).

    Rules, in order:
      1. Already owned by current user → allow, no claim.
      2. Unowned (user_id IS NULL) → allow for anyone, claim on action. This is
         the public-edit-then-claim path for legacy anonymous events.
      3. Legacy creator_token match → allow + claim (lets a user whose browser
         still has the pre-auth token claim events they originally made).
      4. Otherwise → deny.
    """
    if evt.user_id is not None and evt.user_id == current_user.id:
        return True, False
    if evt.user_id is None:
        return True, True
    if provided_token and evt.creator_token and evt.creator_token == provided_token:
        return True, True
    return False, False


def _has_session_overlapping_dt(start_dt, end_dt, exclude_id=None):
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


# --------------------------------------------------------------------------- routes

@calendar_bp.route('/api/events', methods=['GET'])
@login_required
def list_events():
    voter_name = (request.args.get('voter') or '').strip() or current_user.username
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
    return jsonify([
        e.to_dict(voter_name=voter_name, current_user_id=current_user.id) for e in events
    ])


@calendar_bp.route('/api/events', methods=['POST'])
@login_required
def create_event():
    data = request.get_json(silent=True) or {}

    try:
        parsed = _parse_event_payload(data, partial=False)
    except _ValidationError as ve:
        return jsonify({'error': ve.message, **({'code': ve.code} if ve.code else {})}), 400

    if parsed['status'] in CONFLICTING_STATUSES:
        start_dt, end_dt = _compute_dt_range(
            parsed['date'], parsed['end_date'], parsed['all_day'],
            parsed['start_time'], parsed['end_time'],
        )
        if _has_session_overlapping_dt(start_dt, end_dt):
            if not (parsed.get('reasoning') or '').strip():
                return jsonify({
                    'error': 'A session is set for this day — thou must provide a reasoning.',
                    'code': 'reasoning_required',
                }), 400

    # Preserve any client-supplied legacy token so the same client can still
    # edit the event from another tab/session that doesn't yet have a login.
    token = (data.get('creator_token') or '').strip() or secrets.token_hex(32)
    evt = Event(**parsed, user_id=current_user.id, creator_token=token)
    db.session.add(evt)
    db.session.commit()
    return jsonify(evt.to_dict(current_user_id=current_user.id)), 201


@calendar_bp.route('/api/events/<int:event_id>', methods=['PATCH'])
@login_required
def update_event(event_id):
    evt = db.session.get(Event, event_id)
    if not evt:
        return jsonify({'error': 'Entry not found.'}), 404

    data = request.get_json(silent=True) or {}
    provided_token = (data.get('creator_token') or '').strip()
    allowed, should_claim = _check_ownership(evt, provided_token)
    if not allowed:
        return jsonify({'error': 'Thou art not the creator of this entry.'}), 403

    try:
        parsed = _parse_event_payload(data, partial=True)
    except _ValidationError as ve:
        return jsonify({'error': ve.message, **({'code': ve.code} if ve.code else {})}), 400

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

    # Claim the event for the logged-in user (orphan or legacy-token path) so
    # future edits go through the user_id check directly.
    if should_claim:
        evt.user_id = current_user.id

    db.session.commit()
    return jsonify(evt.to_dict(current_user_id=current_user.id))


@calendar_bp.route('/api/events/<int:event_id>', methods=['DELETE'])
@login_required
def delete_event(event_id):
    evt = db.session.get(Event, event_id)
    if not evt:
        return jsonify({'error': 'Entry not found.'}), 404

    data = request.get_json(silent=True) or {}
    provided_token = (data.get('creator_token') or '').strip()
    allowed, _ = _check_ownership(evt, provided_token)
    if not allowed:
        return jsonify({'error': 'Thou art not the creator of this entry.'}), 403

    db.session.delete(evt)
    db.session.commit()
    return '', 204


@calendar_bp.route('/api/session-token', methods=['GET'])
@login_required
def get_session_token():
    """Issue a per-client token for the dual-ownership system. Identity is
    primarily sourced from the logged-in user, but the token is stamped on
    new events as `creator_token` so a client that loses its login (or other
    tabs without a session) can still edit events it created."""
    return jsonify({'token': secrets.token_hex(32)})


@calendar_bp.route('/api/events/<int:event_id>/vote', methods=['POST'])
@login_required
def vote_on_event(event_id):
    evt = db.session.get(Event, event_id)
    if not evt:
        return jsonify({'error': 'Entry not found.'}), 404

    data = request.get_json(silent=True) or {}
    voter_name = current_user.username
    try:
        value = int(data.get('value', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Vote value must be -1, 0, or 1.'}), 400

    if value not in (-1, 0, 1):
        return jsonify({'error': 'Vote value must be -1, 0, or 1.'}), 400

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
            existing.voter_name = voter_name
        else:
            db.session.add(Vote(event_id=event_id, voter_name=voter_name, value=value))

    db.session.commit()
    db.session.refresh(evt)
    return jsonify(evt.to_dict(voter_name=voter_name, current_user_id=current_user.id))
