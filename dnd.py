import json

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_required, current_user

from models import (
    db, Campaign, CampaignMember, Character,
    VALID_RULESETS, make_unique_join_code,
)


dnd_bp = Blueprint('dnd', __name__, url_prefix='/dnd')


# --------------------------------------------------------------------------- helpers

ABS_LEVEL_MIN = 1
ABS_LEVEL_MAX = 20


# Race + class catalogs per ruleset. None signals "no enforced list" (free text).
# Lists are intentionally limited to the canonical Player's Handbook sets so the
# dropdown stays tractable; expansions can be added later.
RULESET_CONTENT = {
    '5e': {
        # Every race / lineage from the official 5e (2014) hardcovers, Plane Shift-adjacent
        # promo PDFs, and standalone releases (Tortle Package, Locathah Rising, Acq. Inc.,
        # Wayfinder's, Eberron, Ravnica, Theros, Strixhaven, Spelljammer, VRGtR, Dragonlance,
        # Witchlight). Subraces that are commonly chosen as standalone in later books
        # (Astral Elf, Eladrin, Sea Elf, Shadar-kai) are listed at the top level for
        # convenience.
        'races': [
            'Aarakocra', 'Aasimar', 'Astral Elf', 'Autognome', 'Bugbear', 'Centaur',
            'Changeling', 'Dhampir', 'Dragonborn', 'Dwarf', 'Eladrin', 'Elf',
            'Fairy', 'Firbolg', 'Genasi', 'Giff', 'Githyanki', 'Githzerai', 'Gnome',
            'Goblin', 'Goliath', 'Grung', 'Hadozee', 'Half-Elf', 'Half-Orc',
            'Halfling', 'Harengon', 'Hexblood', 'Hobgoblin', 'Human', 'Kalashtar',
            'Kender', 'Kenku', 'Kobold', 'Leonin', 'Lizardfolk', 'Locathah',
            'Loxodon', 'Minotaur', 'Orc', 'Owlin', 'Plasmoid', 'Reborn', 'Satyr',
            'Sea Elf', 'Shadar-kai', 'Shifter', 'Simic Hybrid', 'Tabaxi',
            'Thri-kreen', 'Tiefling', 'Tortle', 'Triton', 'Vedalken', 'Verdan',
            'Warforged', 'Yuan-ti',
        ],
        'classes': [
            'Barbarian', 'Bard', 'Cleric', 'Druid', 'Fighter', 'Monk',
            'Paladin', 'Ranger', 'Rogue', 'Sorcerer', 'Warlock', 'Wizard',
            'Artificer',
        ],
    },
    '5.5e': {
        # 2024 PHB species. 5.5e calls them "species" but the model column is
        # still `race` to keep the schema stable across rulesets.
        'races': [
            'Aasimar', 'Dragonborn', 'Dwarf', 'Elf', 'Gnome', 'Goliath',
            'Halfling', 'Human', 'Orc', 'Tiefling',
        ],
        'classes': [
            'Barbarian', 'Bard', 'Cleric', 'Druid', 'Fighter', 'Monk',
            'Paladin', 'Ranger', 'Rogue', 'Sorcerer', 'Warlock', 'Wizard',
        ],
    },
    'other': None,
}


def _ruleset_content(ruleset):
    """Returns {'races': [...], 'classes': [...]} or None for 'other'/unknown."""
    return RULESET_CONTENT.get(ruleset)


# 5e ability scores. (column_name, short_label)
ABILITIES = [
    ('strength',     'STR'),
    ('dexterity',    'DEX'),
    ('constitution', 'CON'),
    ('intelligence', 'INT'),
    ('wisdom',       'WIS'),
    ('charisma',     'CHA'),
]
ABILITY_SHORTS = [s for _, s in ABILITIES]

# Standard 5e skills (same list in 5.5e). (key, label, ability_short)
SKILLS = [
    ('acrobatics',      'Acrobatics',      'DEX'),
    ('animal_handling', 'Animal Handling', 'WIS'),
    ('arcana',          'Arcana',          'INT'),
    ('athletics',       'Athletics',       'STR'),
    ('deception',       'Deception',       'CHA'),
    ('history',         'History',         'INT'),
    ('insight',         'Insight',         'WIS'),
    ('intimidation',    'Intimidation',    'CHA'),
    ('investigation',   'Investigation',   'INT'),
    ('medicine',        'Medicine',        'WIS'),
    ('nature',          'Nature',          'INT'),
    ('perception',      'Perception',      'WIS'),
    ('performance',     'Performance',     'CHA'),
    ('persuasion',      'Persuasion',      'CHA'),
    ('religion',        'Religion',        'INT'),
    ('sleight_of_hand', 'Sleight of Hand', 'DEX'),
    ('stealth',         'Stealth',         'DEX'),
    ('survival',        'Survival',        'WIS'),
]
SKILL_KEYS = [k for k, _, _ in SKILLS]


# --- 5e (and 5.5e where unchanged) class rule data -------------------------
# `skill_choices` is a list of SKILL_KEYS the class may pick from. The special
# value '*' means "any skill" (Bard).
CLASS_RULES_5E = {
    'Barbarian': {
        'save_profs':    ['STR', 'CON'],
        'skill_choices': ['animal_handling', 'athletics', 'intimidation', 'nature', 'perception', 'survival'],
        'skill_count':   2,
        'armor_profs':   ['Light Armor', 'Medium Armor', 'Shields'],
        'weapon_profs':  ['Simple Weapons', 'Martial Weapons'],
        'tool_profs':    [],
        'hit_die':       'd12',
    },
    'Bard': {
        'save_profs':    ['DEX', 'CHA'],
        'skill_choices': '*',
        'skill_count':   3,
        'armor_profs':   ['Light Armor'],
        'weapon_profs':  ['Simple Weapons', 'Hand Crossbows', 'Longswords', 'Rapiers', 'Shortswords'],
        'tool_profs':    ['Three musical instruments of choice'],
        'hit_die':       'd8',
    },
    'Cleric': {
        'save_profs':    ['WIS', 'CHA'],
        'skill_choices': ['history', 'insight', 'medicine', 'persuasion', 'religion'],
        'skill_count':   2,
        'armor_profs':   ['Light Armor', 'Medium Armor', 'Shields'],
        'weapon_profs':  ['Simple Weapons'],
        'tool_profs':    [],
        'hit_die':       'd8',
    },
    'Druid': {
        'save_profs':    ['INT', 'WIS'],
        'skill_choices': ['arcana', 'animal_handling', 'insight', 'medicine', 'nature', 'perception', 'religion', 'survival'],
        'skill_count':   2,
        'armor_profs':   ['Light Armor (non-metal)', 'Medium Armor (non-metal)', 'Shields (non-metal)'],
        'weapon_profs':  ['Clubs', 'Daggers', 'Darts', 'Javelins', 'Maces', 'Quarterstaffs', 'Scimitars', 'Sickles', 'Slings', 'Spears'],
        'tool_profs':    ['Herbalism Kit'],
        'hit_die':       'd8',
    },
    'Fighter': {
        'save_profs':    ['STR', 'CON'],
        'skill_choices': ['acrobatics', 'animal_handling', 'athletics', 'history', 'insight', 'intimidation', 'perception', 'survival'],
        'skill_count':   2,
        'armor_profs':   ['All Armor', 'Shields'],
        'weapon_profs':  ['Simple Weapons', 'Martial Weapons'],
        'tool_profs':    [],
        'hit_die':       'd10',
    },
    'Monk': {
        'save_profs':    ['STR', 'DEX'],
        'skill_choices': ['acrobatics', 'athletics', 'history', 'insight', 'religion', 'stealth'],
        'skill_count':   2,
        'armor_profs':   [],
        'weapon_profs':  ['Simple Weapons', 'Shortswords'],
        'tool_profs':    ["One type of artisan's tools or one musical instrument"],
        'hit_die':       'd8',
    },
    'Paladin': {
        'save_profs':    ['WIS', 'CHA'],
        'skill_choices': ['athletics', 'insight', 'intimidation', 'medicine', 'persuasion', 'religion'],
        'skill_count':   2,
        'armor_profs':   ['All Armor', 'Shields'],
        'weapon_profs':  ['Simple Weapons', 'Martial Weapons'],
        'tool_profs':    [],
        'hit_die':       'd10',
    },
    'Ranger': {
        'save_profs':    ['STR', 'DEX'],
        'skill_choices': ['animal_handling', 'athletics', 'insight', 'investigation', 'nature', 'perception', 'stealth', 'survival'],
        'skill_count':   3,
        'armor_profs':   ['Light Armor', 'Medium Armor', 'Shields'],
        'weapon_profs':  ['Simple Weapons', 'Martial Weapons'],
        'tool_profs':    [],
        'hit_die':       'd10',
    },
    'Rogue': {
        'save_profs':    ['DEX', 'INT'],
        'skill_choices': ['acrobatics', 'athletics', 'deception', 'insight', 'intimidation', 'investigation',
                          'perception', 'performance', 'persuasion', 'sleight_of_hand', 'stealth'],
        'skill_count':   4,
        'armor_profs':   ['Light Armor'],
        'weapon_profs':  ['Simple Weapons', 'Hand Crossbows', 'Longswords', 'Rapiers', 'Shortswords'],
        'tool_profs':    ["Thieves' Tools"],
        'hit_die':       'd8',
    },
    'Sorcerer': {
        'save_profs':    ['CON', 'CHA'],
        'skill_choices': ['arcana', 'deception', 'insight', 'intimidation', 'persuasion', 'religion'],
        'skill_count':   2,
        'armor_profs':   [],
        'weapon_profs':  ['Daggers', 'Darts', 'Slings', 'Quarterstaffs', 'Light Crossbows'],
        'tool_profs':    [],
        'hit_die':       'd6',
    },
    'Warlock': {
        'save_profs':    ['WIS', 'CHA'],
        'skill_choices': ['arcana', 'deception', 'history', 'intimidation', 'investigation', 'nature', 'religion'],
        'skill_count':   2,
        'armor_profs':   ['Light Armor'],
        'weapon_profs':  ['Simple Weapons'],
        'tool_profs':    [],
        'hit_die':       'd8',
    },
    'Wizard': {
        'save_profs':    ['INT', 'WIS'],
        'skill_choices': ['arcana', 'history', 'insight', 'investigation', 'medicine', 'religion'],
        'skill_count':   2,
        'armor_profs':   [],
        'weapon_profs':  ['Daggers', 'Darts', 'Slings', 'Quarterstaffs', 'Light Crossbows'],
        'tool_profs':    [],
        'hit_die':       'd6',
    },
    'Artificer': {
        'save_profs':    ['CON', 'INT'],
        'skill_choices': ['arcana', 'history', 'investigation', 'medicine', 'nature', 'perception', 'sleight_of_hand'],
        'skill_count':   2,
        'armor_profs':   ['Light Armor', 'Medium Armor', 'Shields'],
        'weapon_profs':  ['Simple Weapons'],
        'tool_profs':    ["Thieves' Tools", "Tinker's Tools", "One type of artisan's tools"],
        'hit_die':       'd8',
    },
}

# Races — only covers the canonical PHB-tier + the most common ones. Anything
# not listed here falls back to "no special grants, speed 30".
# `extra_skills_count` = race-granted skill choices (e.g. Half-Elf picks 2).
# `fixed_skills` = automatic skill proficiencies the race always grants.
RACE_RULES_5E = {
    'Dragonborn': {'languages': ['Common', 'Draconic'], 'speed': 30, 'fixed_skills': [], 'extra_skills_count': 0, 'weapon_profs': [], 'tool_profs': []},
    'Dwarf':      {'languages': ['Common', 'Dwarvish'], 'speed': 25, 'fixed_skills': [], 'extra_skills_count': 0, 'weapon_profs': ['Battleaxe', 'Handaxe', 'Light Hammer', 'Warhammer'], 'tool_profs': ["One artisan's tool (Smith's, Brewer's, or Mason's)"]},
    'Elf':        {'languages': ['Common', 'Elvish'],   'speed': 30, 'fixed_skills': ['perception'], 'extra_skills_count': 0, 'weapon_profs': [], 'tool_profs': []},
    'Gnome':      {'languages': ['Common', 'Gnomish'],  'speed': 25, 'fixed_skills': [], 'extra_skills_count': 0, 'weapon_profs': [], 'tool_profs': []},
    'Half-Elf':   {'languages': ['Common', 'Elvish'],   'speed': 30, 'fixed_skills': [], 'extra_skills_count': 2, 'weapon_profs': [], 'tool_profs': []},
    'Half-Orc':   {'languages': ['Common', 'Orc'],      'speed': 30, 'fixed_skills': ['intimidation'], 'extra_skills_count': 0, 'weapon_profs': [], 'tool_profs': []},
    'Halfling':   {'languages': ['Common', 'Halfling'], 'speed': 25, 'fixed_skills': [], 'extra_skills_count': 0, 'weapon_profs': [], 'tool_profs': []},
    'Human':      {'languages': ['Common'],             'speed': 30, 'fixed_skills': [], 'extra_skills_count': 0, 'weapon_profs': [], 'tool_profs': []},
    'Tiefling':   {'languages': ['Common', 'Infernal'], 'speed': 30, 'fixed_skills': [], 'extra_skills_count': 0, 'weapon_profs': [], 'tool_profs': []},
    # 5.5e additions
    'Aasimar':    {'languages': ['Common', 'Celestial'],'speed': 30, 'fixed_skills': [], 'extra_skills_count': 0, 'weapon_profs': [], 'tool_profs': []},
    'Goliath':    {'languages': ['Common', 'Giant'],    'speed': 35, 'fixed_skills': ['athletics'], 'extra_skills_count': 0, 'weapon_profs': [], 'tool_profs': []},
    'Orc':        {'languages': ['Common', 'Orc'],      'speed': 30, 'fixed_skills': [], 'extra_skills_count': 0, 'weapon_profs': [], 'tool_profs': []},
}

# 5.5e class rules are functionally identical to 5e for the proficiency-picker
# purposes this sheet enforces. Reuse the 5e data; differences can be split
# off later if/when they matter.
CLASS_RULES_5_5E = CLASS_RULES_5E
RACE_RULES_5_5E = RACE_RULES_5E

RULESET_RULES = {
    '5e':   {'classes': CLASS_RULES_5E,   'races': RACE_RULES_5E},
    '5.5e': {'classes': CLASS_RULES_5_5E, 'races': RACE_RULES_5_5E},
    'other': None,
}


def _rules_for(ruleset):
    return RULESET_RULES.get(ruleset)


def character_rules(character):
    """Resolve the active class/race rule blocks for this character.

    Returns a dict with keys: class_rules, race_rules, allowed_skill_pool,
    skill_pick_count, save_profs (derived from class), granted_skills,
    granted_languages, granted_armor, granted_weapons, granted_tools, hit_die,
    default_speed. Falls back gracefully when any piece is unknown.
    """
    rules = _rules_for(character.ruleset) or {'classes': {}, 'races': {}}
    cls_rules = rules['classes'].get(character.character_class or '', None)
    race_rules = rules['races'].get(character.race or '', None)

    # Save profs come straight from class (or empty if unknown class).
    save_profs = list(cls_rules['save_profs']) if cls_rules else []

    # Skill pool: class's allowed list (or all 18 for Bard's '*').
    if cls_rules:
        if cls_rules['skill_choices'] == '*':
            pool = list(SKILL_KEYS)
        else:
            pool = list(cls_rules['skill_choices'])
        # Race fixed skills are always available even if not in class pool.
        if race_rules:
            for s in race_rules.get('fixed_skills', []):
                if s not in pool:
                    pool.append(s)
        skill_pick_count = cls_rules['skill_count'] + (race_rules.get('extra_skills_count', 0) if race_rules else 0)
    else:
        # No class chosen — let any skill be selected, no count limit.
        pool = list(SKILL_KEYS)
        skill_pick_count = None

    return {
        'class_rules':       cls_rules,
        'race_rules':        race_rules,
        'allowed_skill_pool': pool,
        'skill_pick_count':  skill_pick_count,
        'save_profs':        save_profs,
        'granted_skills':    list(race_rules.get('fixed_skills', [])) if race_rules else [],
        'granted_languages': list(race_rules.get('languages', [])) if race_rules else [],
        'granted_armor':     list(cls_rules.get('armor_profs', [])) if cls_rules else [],
        'granted_weapons':   (list(cls_rules.get('weapon_profs', [])) if cls_rules else [])
                             + (list(race_rules.get('weapon_profs', [])) if race_rules else []),
        'granted_tools':     (list(cls_rules.get('tool_profs', [])) if cls_rules else [])
                             + (list(race_rules.get('tool_profs', [])) if race_rules else []),
        'hit_die':           cls_rules.get('hit_die') if cls_rules else '',
        'default_speed':     race_rules.get('speed', 30) if race_rules else 30,
    }


def _can_view_character(character):
    """Owner OR campaign DM may see the sheet."""
    return (character.user_id == current_user.id
            or character.campaign.dm_user_id == current_user.id)


def _can_edit_character(character):
    """Only the owner, and only while the campaign is not marked ready."""
    return (character.user_id == current_user.id
            and not character.campaign.is_ready)


def _parse_optional_level(raw, field_label):
    """Returns (value_or_None, error_or_None). Empty string → None (no constraint)."""
    raw = (raw or '').strip()
    if not raw:
        return None, None
    try:
        n = int(raw)
    except ValueError:
        return None, f'{field_label} must be a whole number between {ABS_LEVEL_MIN} and {ABS_LEVEL_MAX}.'
    if n < ABS_LEVEL_MIN or n > ABS_LEVEL_MAX:
        return None, f'{field_label} must be between {ABS_LEVEL_MIN} and {ABS_LEVEL_MAX}.'
    return n, None


def _user_campaigns():
    """Campaigns the current user is involved in (as DM or member)."""
    member_ids = [m.campaign_id for m in current_user.campaign_memberships.all()]
    if member_ids:
        return Campaign.query.filter(
            (Campaign.dm_user_id == current_user.id) | (Campaign.id.in_(member_ids))
        ).order_by(Campaign.created_at.desc()).all()
    return Campaign.query.filter_by(dm_user_id=current_user.id).order_by(Campaign.created_at.desc()).all()


def _ensure_membership(campaign):
    """Verify the current user is the DM or a member; abort 403 otherwise."""
    if campaign.dm_user_id == current_user.id:
        return
    is_member = CampaignMember.query.filter_by(
        campaign_id=campaign.id, user_id=current_user.id
    ).first()
    if not is_member:
        abort(403)


# --------------------------------------------------------------------------- pages

@dnd_bp.route('/')
@login_required
def index():
    campaigns = _user_campaigns()
    return render_template('dnd/index.html', campaigns=campaigns)


@dnd_bp.route('/campaigns/new', methods=['GET', 'POST'])
@login_required
def campaign_new():
    error = None
    form = {}
    if request.method == 'POST':
        form = request.form
        name = (form.get('name') or '').strip()
        description = (form.get('description') or '').strip()
        ruleset = (form.get('default_ruleset') or '5e').strip()
        lvl_min, err_min = _parse_optional_level(form.get('starting_level_min'), 'Min level')
        lvl_max, err_max = _parse_optional_level(form.get('starting_level_max'), 'Max level')

        if not name:
            error = 'A campaign must have a name.'
        elif len(name) > 120:
            error = 'Name too long (max 120 chars).'
        elif ruleset not in VALID_RULESETS:
            error = f'Ruleset must be one of: {", ".join(sorted(VALID_RULESETS))}.'
        elif err_min:
            error = err_min
        elif err_max:
            error = err_max
        elif lvl_min is not None and lvl_max is not None and lvl_min > lvl_max:
            error = 'Min level cannot exceed max level.'
        else:
            campaign = Campaign(
                name=name,
                description=description,
                dm_user_id=current_user.id,
                join_code=make_unique_join_code(),
                default_ruleset=ruleset,
                starting_level_min=lvl_min,
                starting_level_max=lvl_max,
            )
            db.session.add(campaign)
            db.session.flush()
            # Auto-add DM as a member so list/permission queries are uniform.
            # Idempotent in case a stale row already exists (e.g. from a
            # prior deploy where the user joined this campaign id manually).
            existing_member = CampaignMember.query.filter_by(
                campaign_id=campaign.id, user_id=current_user.id
            ).first()
            if not existing_member:
                db.session.add(CampaignMember(
                    campaign_id=campaign.id, user_id=current_user.id,
                ))
            db.session.commit()
            return redirect(url_for('dnd.campaign_detail', campaign_id=campaign.id))

    return render_template('dnd/campaign_new.html',
                           error=error, rulesets=sorted(VALID_RULESETS), form=form)


@dnd_bp.route('/campaigns/join', methods=['GET', 'POST'])
@login_required
def campaign_join():
    error = None
    code_prefill = (request.args.get('code') or '').strip().upper()
    if request.method == 'POST':
        code = (request.form.get('join_code') or '').strip().upper()
        if not code:
            error = 'A code is required.'
        else:
            campaign = Campaign.query.filter_by(join_code=code).first()
            if not campaign:
                error = 'No campaign hath that code.'
            else:
                existing = CampaignMember.query.filter_by(
                    campaign_id=campaign.id, user_id=current_user.id
                ).first()
                if not existing:
                    db.session.add(CampaignMember(campaign_id=campaign.id, user_id=current_user.id))
                    db.session.commit()
                flash(f'Thou hast joined "{campaign.name}".', 'success')
                return redirect(url_for('dnd.campaign_detail', campaign_id=campaign.id))

    return render_template('dnd/campaign_join.html', error=error, code_prefill=code_prefill)


@dnd_bp.route('/campaigns/<int:campaign_id>')
@login_required
def campaign_detail(campaign_id):
    campaign = db.session.get(Campaign, campaign_id)
    if not campaign:
        abort(404)
    _ensure_membership(campaign)

    members = (CampaignMember.query
               .filter_by(campaign_id=campaign.id)
               .join(CampaignMember.user)
               .order_by(CampaignMember.joined_at.asc())
               .all())
    characters = (Character.query
                  .filter_by(campaign_id=campaign.id)
                  .order_by(Character.created_at.asc())
                  .all())
    is_dm = campaign.dm_user_id == current_user.id
    return render_template(
        'dnd/campaign_detail.html',
        campaign=campaign, members=members, characters=characters, is_dm=is_dm,
    )


@dnd_bp.route('/campaigns/<int:campaign_id>/state.json')
@login_required
def campaign_state(campaign_id):
    """Lightweight JSON snapshot of a campaign's roster + characters for polling."""
    campaign = db.session.get(Campaign, campaign_id)
    if not campaign:
        abort(404)
    _ensure_membership(campaign)

    members = (CampaignMember.query
               .filter_by(campaign_id=campaign.id)
               .order_by(CampaignMember.joined_at.asc())
               .all())
    characters = (Character.query
                  .filter_by(campaign_id=campaign.id)
                  .order_by(Character.created_at.asc())
                  .all())
    return jsonify({
        'members': [
            {
                'user_id': m.user_id,
                'username': m.user.username,
                'is_dm': m.user_id == campaign.dm_user_id,
            }
            for m in members
        ],
        'characters': [
            {
                'id': c.id,
                'name': c.name,
                'level': c.level,
                'race': c.race or '',
                'character_class': c.character_class or '',
                'player_username': c.user.username,
                # Owner + DM may click through; others see the row only.
                'viewable_by_me': (c.user_id == current_user.id
                                   or campaign.dm_user_id == current_user.id),
            }
            for c in characters
        ],
    })


@dnd_bp.route('/campaigns/<int:campaign_id>/ready', methods=['POST'])
@login_required
def campaign_toggle_ready(campaign_id):
    """DM-only toggle that locks/unlocks character sheet edits."""
    campaign = db.session.get(Campaign, campaign_id)
    if not campaign:
        abort(404)
    if campaign.dm_user_id != current_user.id:
        abort(403)
    campaign.is_ready = not campaign.is_ready
    db.session.commit()
    flash(
        'Sheets are locked. The campaign hath begun.' if campaign.is_ready
        else 'Sheets are unlocked for editing.',
        'info',
    )
    return redirect(url_for('dnd.campaign_detail', campaign_id=campaign.id))


@dnd_bp.route('/campaigns/<int:campaign_id>/delete', methods=['POST'])
@login_required
def campaign_delete(campaign_id):
    campaign = db.session.get(Campaign, campaign_id)
    if not campaign:
        abort(404)
    if campaign.dm_user_id != current_user.id:
        abort(403)
    db.session.delete(campaign)
    db.session.commit()
    flash(f'Campaign "{campaign.name}" hath been disbanded.', 'info')
    return redirect(url_for('dnd.index'))


# --------------------------------------------------------------------------- characters

@dnd_bp.route('/campaigns/<int:campaign_id>/characters/new', methods=['GET', 'POST'])
@login_required
def character_new(campaign_id):
    campaign = db.session.get(Campaign, campaign_id)
    if not campaign:
        abort(404)
    _ensure_membership(campaign)

    # Effective level bounds for this campaign's character creation.
    lvl_min = campaign.starting_level_min or ABS_LEVEL_MIN
    lvl_max = campaign.starting_level_max or ABS_LEVEL_MAX

    # Ruleset is locked to the campaign's. We don't accept a different one from
    # the form — that'd just be a player picking their own rulebook.
    ruleset = campaign.default_ruleset
    content = _ruleset_content(ruleset)
    race_options = content['races'] if content else None
    class_options = content['classes'] if content else None

    error = None
    form = {}
    if request.method == 'POST':
        form = request.form
        name = (form.get('name') or '').strip()
        char_class = (form.get('character_class') or '').strip()
        race = (form.get('race') or '').strip()
        try:
            level = int(form.get('level') or lvl_min)
        except ValueError:
            level = lvl_min

        if not name:
            error = 'A hero must have a name.'
        elif level < lvl_min or level > lvl_max:
            if lvl_min == lvl_max:
                error = f'The Dungeon Master requires new heroes to begin at level {lvl_min}.'
            else:
                error = f'New heroes must begin between levels {lvl_min} and {lvl_max} in this campaign.'
        elif race_options is not None and race and race not in race_options:
            error = f'"{race}" is not a recognized race in this campaign’s ruleset.'
        elif class_options is not None and char_class and char_class not in class_options:
            error = f'"{char_class}" is not a recognized class in this campaign’s ruleset.'
        else:
            character = Character(
                user_id=current_user.id,
                campaign_id=campaign.id,
                name=name,
                race=race,
                character_class=char_class,
                level=level,
                ruleset=ruleset,
            )
            character.proficiency_bonus = character.proficiency_bonus_from_level
            db.session.add(character)
            db.session.commit()
            return redirect(url_for('dnd.character_view', character_id=character.id))

    return render_template(
        'dnd/character_new.html',
        campaign=campaign, error=error, form=form,
        lvl_min=lvl_min, lvl_max=lvl_max,
        ruleset=ruleset,
        race_options=race_options, class_options=class_options,
    )


def _clamp_int(raw, lo, hi, default):
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _parse_lines(raw):
    """Split a textarea value into a deduped list of non-empty trimmed lines."""
    if not raw:
        return []
    seen, out = set(), []
    for line in raw.replace('\r', '').split('\n'):
        line = line.strip()
        if line and line not in seen:
            seen.add(line)
            out.append(line)
    return out


def _checked_in(form, name):
    """Form fields with name=X yield a list; True if anything was checked."""
    return name in form


@dnd_bp.route('/characters/<int:character_id>', methods=['GET', 'POST'])
@login_required
def character_view(character_id):
    character = db.session.get(Character, character_id)
    if not character:
        abort(404)
    _ensure_membership(character.campaign)
    if not _can_view_character(character):
        abort(403)

    is_mine = character.user_id == current_user.id
    is_dm = character.campaign.dm_user_id == current_user.id
    editable = _can_edit_character(character)

    if request.method == 'POST':
        if not editable:
            abort(403)
        form = request.form
        _apply_sheet_v1_update(character, form)
        db.session.commit()
        flash('Thy sheet hath been inscribed.', 'success')
        return redirect(url_for('dnd.character_view', character_id=character.id))

    # Resolve ruleset content (for dropdowns) and rule data (for constraints).
    content = _ruleset_content(character.ruleset)
    race_options = content['races'] if content else None
    class_options = content['classes'] if content else None
    rules = character_rules(character)

    # When the class is known, save proficiencies are derived; storage may
    # contain stale entries from before the user picked a class. Display only
    # the derived set so the UI is always honest.
    if rules['class_rules']:
        display_save_profs = set(rules['save_profs'])
    else:
        display_save_profs = set(character.get_list('save_proficiencies'))

    # Filter skill proficiencies to what's actually allowed for the character.
    stored_skill_profs = set(character.get_list('skill_proficiencies'))
    allowed_pool = set(rules['allowed_skill_pool'])
    display_skill_profs = stored_skill_profs & allowed_pool
    display_skill_experts = set(character.get_list('skill_expertise')) & display_skill_profs

    return render_template(
        'dnd/character_sheet.html',
        character=character, campaign=character.campaign,
        is_mine=is_mine, is_dm=is_dm, editable=editable,
        abilities=ABILITIES, skills=SKILLS, ability_shorts=ABILITY_SHORTS,
        save_profs=display_save_profs,
        skill_profs=display_skill_profs,
        skill_experts=display_skill_experts,
        languages=character.get_list('languages'),
        tool_profs=character.get_list('tool_proficiencies'),
        armor_profs=character.get_list('armor_proficiencies'),
        weapon_profs=character.get_list('weapon_proficiencies'),
        death_saves=character.get_map('death_saves') or {'successes': 0, 'failures': 0},
        race_options=race_options, class_options=class_options,
        rules=rules,
        # JSON-serializable rule data for the JS layer so race/class changes
        # can update constraints client-side without a page reload.
        rule_data_json=json.dumps({
            'classes': (RULESET_RULES.get(character.ruleset, {}) or {}).get('classes', {}),
            'races':   (RULESET_RULES.get(character.ruleset, {}) or {}).get('races', {}),
            'skill_keys': SKILL_KEYS,
        }),
    )


def _apply_sheet_v1_update(character, form):
    """Pull every Sheet v1 field out of the submitted form and write to the
    character. Field validation is permissive — we clamp to sensible bounds
    rather than reject, so a typo doesn't lose the rest of the save."""

    # --- Identity
    character.name = (form.get('name') or character.name).strip()[:120]
    character.race = (form.get('race') or '').strip()[:80]
    character.subrace = (form.get('subrace') or '').strip()[:80]
    character.character_class = (form.get('character_class') or '').strip()[:80]
    character.subclass = (form.get('subclass') or '').strip()[:80]
    character.background = (form.get('background') or '').strip()[:80]
    character.alignment = (form.get('alignment') or '').strip()[:40]
    character.level = _clamp_int(form.get('level'), 1, 20, character.level or 1)
    character.experience_points = _clamp_int(form.get('experience_points'), 0, 1_000_000, 0)

    # --- Abilities
    for col, _ in ABILITIES:
        setattr(character, col, _clamp_int(form.get(col), 1, 30, getattr(character, col) or 10))

    # --- Combat
    character.max_hp     = _clamp_int(form.get('max_hp'), 0, 999, character.max_hp or 0)
    character.current_hp = _clamp_int(form.get('current_hp'), -99, character.max_hp, character.current_hp or 0)
    character.temp_hp    = _clamp_int(form.get('temp_hp'), 0, 999, 0)
    character.armor_class      = _clamp_int(form.get('armor_class'), 0, 40, character.armor_class or 10)
    character.initiative_bonus = _clamp_int(form.get('initiative_bonus'), -10, 30, character.initiative_bonus or 0)
    character.speed            = _clamp_int(form.get('speed'), 0, 200, character.speed or 30)

    # Proficiency bonus: auto from level if the user didn't override.
    pb_override = (form.get('proficiency_bonus') or '').strip()
    if pb_override == '' or form.get('proficiency_bonus_auto') == 'on':
        character.proficiency_bonus = character.proficiency_bonus_from_level
    else:
        character.proficiency_bonus = _clamp_int(pb_override, 1, 12, character.proficiency_bonus_from_level)

    character.hit_dice      = (form.get('hit_dice') or character.hit_dice or '').strip()[:40]
    character.hit_dice_used = _clamp_int(form.get('hit_dice_used'), 0, 40, 0)

    # Death saves (3 checkboxes each)
    succ = sum(1 for i in (1, 2, 3) if f'death_success_{i}' in form)
    fail = sum(1 for i in (1, 2, 3) if f'death_failure_{i}' in form)
    character.death_saves_json = json.dumps({'successes': succ, 'failures': fail})

    # --- Rule-derived values: race/class drive saves, skill pool, and granted profs.
    # The user can't override save proficiencies — they come from class. If
    # the class isn't recognized we fall back to whatever the form sent.
    rules = character_rules(character)
    if rules['class_rules']:
        character.set_list('save_proficiencies', rules['save_profs'])
    else:
        save_profs = [s for s in ABILITY_SHORTS if f'save_prof_{s}' in form]
        character.set_list('save_proficiencies', save_profs)

    # Skill proficiencies: filter to the allowed pool, clamp to max count.
    raw_skill_profs = [k for k in SKILL_KEYS if f'skill_prof_{k}' in form]
    allowed_pool = set(rules['allowed_skill_pool'])
    valid_skill_profs = [k for k in raw_skill_profs if k in allowed_pool]
    # Race-granted "fixed" skills are always included even if user unchecked.
    for s in rules['granted_skills']:
        if s not in valid_skill_profs:
            valid_skill_profs.append(s)
    # Enforce the class's pick count (granted skills don't count against it).
    if rules['skill_pick_count'] is not None:
        granted = set(rules['granted_skills'])
        chosen = [s for s in valid_skill_profs if s not in granted]
        if len(chosen) > rules['skill_pick_count']:
            chosen = chosen[:rules['skill_pick_count']]
        valid_skill_profs = sorted(set(chosen) | granted)
    character.set_list('skill_proficiencies', valid_skill_profs)

    # Expertise: only valid if the skill is also proficient.
    raw_experts = [k for k in SKILL_KEYS if f'skill_expert_{k}' in form]
    valid_experts = [k for k in raw_experts if k in valid_skill_profs]
    character.set_list('skill_expertise', valid_experts)

    # --- Profs / languages: merge race+class grants with user's "additional" entries.
    # The textarea only holds the user-added extras (granted items are displayed
    # separately as a locked list); the saved column stores everything.
    def _merge_granted(granted, raw):
        seen, out = set(), []
        for item in list(granted) + _parse_lines(raw):
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    character.set_list('languages',            _merge_granted(rules['granted_languages'], form.get('languages')))
    character.set_list('tool_proficiencies',   _merge_granted(rules['granted_tools'],     form.get('tool_proficiencies')))
    character.set_list('armor_proficiencies',  _merge_granted(rules['granted_armor'],     form.get('armor_proficiencies')))
    character.set_list('weapon_proficiencies', _merge_granted(rules['granted_weapons'],   form.get('weapon_proficiencies')))


@dnd_bp.route('/characters/<int:character_id>/delete', methods=['POST'])
@login_required
def character_delete(character_id):
    character = db.session.get(Character, character_id)
    if not character:
        abort(404)
    if character.user_id != current_user.id and character.campaign.dm_user_id != current_user.id:
        abort(403)
    campaign_id = character.campaign_id
    name = character.name
    db.session.delete(character)
    db.session.commit()
    flash(f'"{name}" hath fallen from the ledger.', 'info')
    return redirect(url_for('dnd.campaign_detail', campaign_id=campaign_id))
