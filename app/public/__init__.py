from flask import Blueprint

bp = Blueprint("public", __name__, url_prefix="")
from . import routes  # noqa
