import _init_paths  # noqa: F401
from core.config import update_config


def main():
    update_config("experiment.yaml")


if __name__ == "__main__":
    main()
