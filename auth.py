import re
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user

from models import db, User


auth_bp = Blueprint('auth', __name__)

USERNAME_RE = re.compile(r'^[A-Za-z0-9_-]{3,40}$')
MIN_PASSWORD_LEN = 6


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('calendar.index'))

    error = None
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        if not username or not password:
            error = 'Speak thy name and thy passphrase.'
        else:
            user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
            if user is None or not user.check_password(password):
                error = 'Those credentials shall not pass.'
            else:
                login_user(user, remember=True)
                next_url = request.args.get('next') or url_for('calendar.index')
                return redirect(next_url)

    return render_template('auth/login.html', error=error)


@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('calendar.index'))

    error = None
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        confirm = request.form.get('confirm') or ''

        if not USERNAME_RE.match(username):
            error = 'Name must be 3-40 chars, letters/numbers/_/- only.'
        elif len(password) < MIN_PASSWORD_LEN:
            error = f'Passphrase must be at least {MIN_PASSWORD_LEN} characters.'
        elif password != confirm:
            error = 'Passphrases do not match.'
        elif User.query.filter(db.func.lower(User.username) == username.lower()).first():
            error = 'That name is already claimed.'
        else:
            user = User(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user, remember=True)
            return redirect(url_for('calendar.index'))

    return render_template('auth/signup.html', error=error)


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
