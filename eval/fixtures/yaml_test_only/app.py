"""Production application code never imports or calls yaml."""


def get_version():
    return "1.0.0"


if __name__ == "__main__":
    print(get_version())
