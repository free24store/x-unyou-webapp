from flask import Blueprint

bp = Blueprint("sns", __name__, url_prefix="/sns")

from . import routes  # noqa
