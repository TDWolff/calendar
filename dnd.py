from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_required, current_user

from models import (
    db, Campaign, CampaignMember, Character,
    VALID_RULESETS, make_unique_join_code,
)


dnd_bp = Blueprint('dnd', __name__, url_prefix='/dnd')


# --------------------------------------------------------------------------- helpers

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
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        description = (request.form.get('description') or '').strip()
        ruleset = (request.form.get('default_ruleset') or '5e').strip()
        if not name:
            error = 'A campaign must have a name.'
        elif len(name) > 120:
            error = 'Name too long (max 120 chars).'
        elif ruleset not in VALID_RULESETS:
            error = f'Ruleset must be one of: {", ".join(sorted(VALID_RULESETS))}.'
        else:
            campaign = Campaign(
                name=name,
                description=description,
                dm_user_id=current_user.id,
                join_code=make_unique_join_code(),
                default_ruleset=ruleset,
            )
            db.session.add(campaign)
            db.session.flush()
            # Auto-add DM as a member so list/permission queries are uniform
            db.session.add(CampaignMember(campaign_id=campaign.id, user_id=current_user.id))
            db.session.commit()
            return redirect(url_for('dnd.campaign_detail', campaign_id=campaign.id))

    return render_template('dnd/campaign_new.html', error=error, rulesets=sorted(VALID_RULESETS))


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

    error = None
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        char_class = (request.form.get('character_class') or '').strip()
        race = (request.form.get('race') or '').strip()
        try:
            level = max(1, min(20, int(request.form.get('level') or 1)))
        except ValueError:
            level = 1
        ruleset = (request.form.get('ruleset') or campaign.default_ruleset).strip()

        if not name:
            error = 'A hero must have a name.'
        elif ruleset not in VALID_RULESETS:
            error = f'Ruleset must be one of: {", ".join(sorted(VALID_RULESETS))}.'
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
        campaign=campaign, error=error, rulesets=sorted(VALID_RULESETS),
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
