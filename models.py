import json
import secrets
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


# ============================================================================
# D&D
# ============================================================================

VALID_RULESETS = {'5e', '5.5e', 'other'}


def _json_dump(value):
    if value is None:
        return None
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return None


def _json_load(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _generate_join_code():
    # 8 unambiguous chars — no I/1/O/0 confusion
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return ''.join(secrets.choice(alphabet) for _ in range(8))


class Campaign(db.Model):
    __tablename__ = 'campaigns'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default='')
    dm_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    join_code = db.Column(db.String(16), nullable=False, unique=True, index=True)
    default_ruleset = db.Column(db.String(20), nullable=False, default='5e')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    dm = db.relationship('User', backref=db.backref('campaigns_dm', lazy='dynamic'))

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description or '',
            'dm_user_id': self.dm_user_id,
            'dm_username': self.dm.username if self.dm else None,
            'join_code': self.join_code,
            'default_ruleset': self.default_ruleset,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class CampaignMember(db.Model):
    __tablename__ = 'campaign_members'

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(
        db.Integer,
        db.ForeignKey('campaigns.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)

    campaign = db.relationship(
        'Campaign',
        backref=db.backref('members', cascade='all, delete-orphan', passive_deletes=True, lazy='dynamic'),
    )
    user = db.relationship('User', backref=db.backref('campaign_memberships', lazy='dynamic'))

    __table_args__ = (
        UniqueConstraint('campaign_id', 'user_id', name='_campaign_user_uc'),
    )


class Character(db.Model):
    __tablename__ = 'characters'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    campaign_id = db.Column(
        db.Integer,
        db.ForeignKey('campaigns.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # --- Identity
    name = db.Column(db.String(120), nullable=False)
    ruleset = db.Column(db.String(20), nullable=False, default='5e')
    race = db.Column(db.String(80), default='')
    subrace = db.Column(db.String(80), default='')
    character_class = db.Column(db.String(80), default='')
    subclass = db.Column(db.String(80), default='')
    level = db.Column(db.Integer, default=1)
    background = db.Column(db.String(80), default='')
    alignment = db.Column(db.String(40), default='')
    experience_points = db.Column(db.Integer, default=0)

    # --- Ability scores (1-30 range; default 10)
    strength = db.Column(db.Integer, default=10)
    dexterity = db.Column(db.Integer, default=10)
    constitution = db.Column(db.Integer, default=10)
    intelligence = db.Column(db.Integer, default=10)
    wisdom = db.Column(db.Integer, default=10)
    charisma = db.Column(db.Integer, default=10)

    # --- Combat
    max_hp = db.Column(db.Integer, default=10)
    current_hp = db.Column(db.Integer, default=10)
    temp_hp = db.Column(db.Integer, default=0)
    armor_class = db.Column(db.Integer, default=10)
    initiative_bonus = db.Column(db.Integer, default=0)
    speed = db.Column(db.Integer, default=30)
    proficiency_bonus = db.Column(db.Integer, default=2)
    hit_dice = db.Column(db.String(40), default='1d8')   # e.g. "5d8"
    hit_dice_used = db.Column(db.Integer, default=0)

    # --- Spells (core)
    spellcasting_ability = db.Column(db.String(20), default='')  # 'INT' | 'WIS' | 'CHA' | ''
    spell_save_dc = db.Column(db.Integer, default=0)
    spell_attack_bonus = db.Column(db.Integer, default=0)

    # --- Currency
    copper = db.Column(db.Integer, default=0)
    silver = db.Column(db.Integer, default=0)
    electrum = db.Column(db.Integer, default=0)
    gold = db.Column(db.Integer, default=0)
    platinum = db.Column(db.Integer, default=0)

    # --- JSON-encoded lists/maps (stored as TEXT)
    save_proficiencies_json = db.Column(db.Text, default='[]')
    skill_proficiencies_json = db.Column(db.Text, default='[]')
    skill_expertise_json = db.Column(db.Text, default='[]')
    languages_json = db.Column(db.Text, default='[]')
    tool_proficiencies_json = db.Column(db.Text, default='[]')
    armor_proficiencies_json = db.Column(db.Text, default='[]')
    weapon_proficiencies_json = db.Column(db.Text, default='[]')
    spell_slots_json = db.Column(db.Text, default='{}')        # {"1": {"max": 4, "used": 0}, ...}
    spells_known_json = db.Column(db.Text, default='[]')       # [{"name", "level", "prepared"}]
    inventory_json = db.Column(db.Text, default='[]')          # [{"name", "qty", "weight", "equipped", "notes"}]
    features_traits_json = db.Column(db.Text, default='[]')    # [{"name", "source", "description"}]
    feats_json = db.Column(db.Text, default='[]')              # [{"name", "description"}]
    conditions_json = db.Column(db.Text, default='[]')         # ["poisoned", "frightened", ...]
    death_saves_json = db.Column(db.Text, default='{"successes":0,"failures":0}')

    # --- Free text
    personality_traits = db.Column(db.Text, default='')
    ideals = db.Column(db.Text, default='')
    bonds = db.Column(db.Text, default='')
    flaws = db.Column(db.Text, default='')
    appearance = db.Column(db.Text, default='')
    backstory = db.Column(db.Text, default='')
    notes = db.Column(db.Text, default='')

    user = db.relationship('User', backref=db.backref('characters', lazy='dynamic'))
    campaign = db.relationship('Campaign', backref=db.backref('characters', cascade='all, delete-orphan', passive_deletes=True, lazy='dynamic'))

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def ability_modifier(score):
        try:
            return (int(score) - 10) // 2
        except (TypeError, ValueError):
            return 0

    @property
    def modifiers(self):
        return {
            'STR': self.ability_modifier(self.strength),
            'DEX': self.ability_modifier(self.dexterity),
            'CON': self.ability_modifier(self.constitution),
            'INT': self.ability_modifier(self.intelligence),
            'WIS': self.ability_modifier(self.wisdom),
            'CHA': self.ability_modifier(self.charisma),
        }

    @property
    def proficiency_bonus_from_level(self):
        # 5e standard: +2 at 1-4, +3 at 5-8, +4 at 9-12, +5 at 13-16, +6 at 17+
        lvl = max(1, min(20, int(self.level or 1)))
        return 2 + ((lvl - 1) // 4)

    def get_list(self, field):
        return _json_load(getattr(self, f'{field}_json', '[]'), [])

    def set_list(self, field, value):
        setattr(self, f'{field}_json', _json_dump(value or []) or '[]')

    def get_map(self, field):
        return _json_load(getattr(self, f'{field}_json', '{}'), {})

    def set_map(self, field, value):
        setattr(self, f'{field}_json', _json_dump(value or {}) or '{}')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'player_username': self.user.username if self.user else None,
            'campaign_id': self.campaign_id,
            'name': self.name,
            'ruleset': self.ruleset,
            'race': self.race or '',
            'subrace': self.subrace or '',
            'character_class': self.character_class or '',
            'subclass': self.subclass or '',
            'level': self.level or 1,
            'background': self.background or '',
            'alignment': self.alignment or '',
            'experience_points': self.experience_points or 0,
            'abilities': {
                'strength': self.strength, 'dexterity': self.dexterity, 'constitution': self.constitution,
                'intelligence': self.intelligence, 'wisdom': self.wisdom, 'charisma': self.charisma,
            },
            'modifiers': self.modifiers,
            'combat': {
                'max_hp': self.max_hp, 'current_hp': self.current_hp, 'temp_hp': self.temp_hp,
                'armor_class': self.armor_class, 'initiative_bonus': self.initiative_bonus,
                'speed': self.speed, 'proficiency_bonus': self.proficiency_bonus,
                'hit_dice': self.hit_dice, 'hit_dice_used': self.hit_dice_used,
            },
            'spellcasting': {
                'ability': self.spellcasting_ability or '',
                'save_dc': self.spell_save_dc, 'attack_bonus': self.spell_attack_bonus,
                'slots': self.get_map('spell_slots'),
                'spells_known': self.get_list('spells_known'),
            },
            'currency': {
                'cp': self.copper, 'sp': self.silver, 'ep': self.electrum,
                'gp': self.gold, 'pp': self.platinum,
            },
            'save_proficiencies': self.get_list('save_proficiencies'),
            'skill_proficiencies': self.get_list('skill_proficiencies'),
            'skill_expertise': self.get_list('skill_expertise'),
            'languages': self.get_list('languages'),
            'tool_proficiencies': self.get_list('tool_proficiencies'),
            'armor_proficiencies': self.get_list('armor_proficiencies'),
            'weapon_proficiencies': self.get_list('weapon_proficiencies'),
            'inventory': self.get_list('inventory'),
            'features_traits': self.get_list('features_traits'),
            'feats': self.get_list('feats'),
            'conditions': self.get_list('conditions'),
            'death_saves': _json_load(self.death_saves_json, {'successes': 0, 'failures': 0}),
            'personality_traits': self.personality_traits or '',
            'ideals': self.ideals or '',
            'bonds': self.bonds or '',
            'flaws': self.flaws or '',
            'appearance': self.appearance or '',
            'backstory': self.backstory or '',
            'notes': self.notes or '',
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


def make_unique_join_code():
    """Generate a join_code not already in the campaigns table."""
    for _ in range(20):
        code = _generate_join_code()
        if not Campaign.query.filter_by(join_code=code).first():
            return code
    # Astronomically unlikely fallthrough
    return _generate_join_code() + secrets.token_hex(2).upper()


# ============================================================================


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
