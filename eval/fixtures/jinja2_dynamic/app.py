"""Dynamic template engine dispatching via registry lookup."""

import os

DISPATCH = {
    "jinja": "jinja2.Environment",
    "plain": "str",
}


def render_dynamic(source):
    choice = os.environ.get("ENGINE", "plain")
    cls_name = DISPATCH.get(choice)
    return f"rendered with {cls_name}: {source}"


if __name__ == "__main__":
    render_dynamic("test")
