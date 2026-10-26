"""Re-export via __init__."""

from pkg import parse_yaml


def run():
    return parse_yaml("count: 5")


if __name__ == "__main__":
    run()
