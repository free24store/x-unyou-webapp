from flask import Blueprint

bp = Blueprint("master", __name__, url_prefix="/master")

from . import routes  # noqa
