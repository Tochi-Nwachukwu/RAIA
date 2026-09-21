"""Edit config YAML in place without losing its comments."""

from collections.abc import Callable
from pathlib import Path

from ruamel.yaml import YAML


def update_yaml(path: Path, mutate: Callable[[dict], None]) -> None:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 110
    data = yaml.load(path.read_text())
    mutate(data)
    with path.open("w") as f:
        yaml.dump(data, f)
