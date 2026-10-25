import yaml as y
import yaml.constructor
import yaml.loader as yl
from yaml import load as ld
from yaml import safe_load
import os.path as osp


def via_module_alias(text):
    return y.load(text)


def via_from_alias(text):
    return ld(text)


def via_plain_from(text):
    return safe_load(text)


def via_dotted_import():
    return yaml.constructor.FullConstructor


def via_dotted_alias():
    return yl.Loader


def passes_reference(items):
    return list(map(yaml.load, items))


def function_level_import(text):
    import jinja2 as j2

    return j2.Template(text)


def shadowed(yaml):
    return yaml.load("not the library")


def unrelated():
    return osp.join("a", "b")
