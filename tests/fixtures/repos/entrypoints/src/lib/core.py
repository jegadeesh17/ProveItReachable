import yaml


class Service:
    def __init__(self):
        self.ready = True

    def run(self, text):
        self.helper(text)
        return self.missing(text)

    def helper(self, text):
        def inner():
            return yaml.load(text)

        return inner()
