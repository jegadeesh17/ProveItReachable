import yaml


def update_config(config_file):
    with open(config_file) as f:
        return yaml.load(f, Loader=yaml.FullLoader)
