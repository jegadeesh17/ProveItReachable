def run(text):
    import yaml

    return yaml.safe_load(text)


class Runner:
    def go(self):
        return run("a")
