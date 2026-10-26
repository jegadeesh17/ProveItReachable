"""Imports jinja2 but only calls escape, never Environment or template rendering."""

import jinja2


def clean_html(text):
    return jinja2.escape(text)


if __name__ == "__main__":
    print(clean_html("<script>"))
