"""Direct use of Jinja2 template rendering."""

import jinja2


def render_template(user_input):
    env = jinja2.Environment()
    template = env.from_string(user_input)
    return template.render()


if __name__ == "__main__":
    print(render_template("Hello {{ name }}"))
