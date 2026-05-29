from flask import Blueprint, render_template
from flask_login import login_required


dnd_bp = Blueprint('dnd', __name__, url_prefix='/dnd')


@dnd_bp.route('/')
@login_required
def index():
    return render_template('dnd/index.html')
