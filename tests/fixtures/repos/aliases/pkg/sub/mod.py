from .. import helpers
from ..helpers import run as r
from . import sibling
from ... import beyond_the_repo


def call_all():
    helpers.run("a")
    r("b")
    return sibling
