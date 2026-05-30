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
            }
            for c in characters
        ],
    })


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


@dnd_bp.route('/characters/<int:character_id>')
@login_required
def character_view(character_id):
    character = db.session.get(Character, character_id)
    if not character:
        abort(404)
    _ensure_membership(character.campaign)
    is_mine = character.user_id == current_user.id
    is_dm = character.campaign.dm_user_id == current_user.id
    return render_template(
        'dnd/character_view.html',
        character=character, campaign=character.campaign,
        is_mine=is_mine, is_dm=is_dm,
    )


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
