import yaml


def load_config(text):
    return yaml.full_load(text)


if __name__ == "__main__":
    print(load_config("a: 1"))
