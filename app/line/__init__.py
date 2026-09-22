from flask import Blueprint

bp = Blueprint("line", __name__, url_prefix="/line")

from . import routes  # noqa: E402, F401
