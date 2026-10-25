"""Prints the version of the installed wheel package."""

import wheel


def describe():
    return f"wheel {wheel.__version__}"


if __name__ == "__main__":
    print(describe())
