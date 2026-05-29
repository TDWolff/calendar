import os
from flask import Flask
from flask_login import LoginManager

from models import db, User, migrate_schema
from auth import auth_bp
from calendar_bp import calendar_bp
from dnd import dnd_bp


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
app.secret_key = os.environ.get('SECRET_KEY') or 'dev-secret-change-me-in-prod'

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Thou must sign in to enter the ledger.'


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


app.register_blueprint(auth_bp)
app.register_blueprint(calendar_bp)
app.register_blueprint(dnd_bp)


@app.route('/healthz')
def healthz():
    return {'ok': True}


with app.app_context():
    db.create_all()
    migrate_schema()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8093, debug=True)
