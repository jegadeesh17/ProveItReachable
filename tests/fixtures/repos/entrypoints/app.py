import click
import yaml
from flask import Flask

app = Flask(__name__)


@app.route("/load", methods=["POST"])
def load_view():
    return parse_body("a: 1")


@app.get("/health")
def health():
    return "ok"


@click.group()
def cli():
    pass


@cli.command()
def sync():
    parse_body("b: 2")


def parse_body(text):
    return yaml.load(text)


def main():
    cli()


if __name__ == "__main__":
    main()
