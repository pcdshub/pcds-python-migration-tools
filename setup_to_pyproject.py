from __future__ import annotations

import configparser
import os
import pathlib
import sys
from collections.abc import Sequence
from types import ModuleType
from typing import Any

import setuptools as _setuptools
import toml

script_path = pathlib.Path(__file__).resolve().parent
cookiecutter_root = (
    script_path / "cookiecutter-pcds-python" / "{{ cookiecutter.folder_name }}"
)
cookiecutter_import_path = cookiecutter_root / "{{ cookiecutter.import_name }}"


class SetuptoolsStandin(ModuleType):
    _setup_result = None

    def get_last_setup_kwargs(self) -> dict[str, Any] | None:
        return self._setup_result

    def find_packages(self, *args, **kwargs):
        return _setuptools.find_packages(*args, **kwargs)

    def setup(self, **kwargs):
        self._setup_result = kwargs

    def __getattr__(self, name: str) -> Any:
        return getattr(_setuptools, name)


setuptools = SetuptoolsStandin(name="setuptools")
sys.modules["setuptools"] = setuptools


def get_pyproject_template() -> dict[str, Any]:
    with open(cookiecutter_root / "pyproject.toml") as fp:
        contents = fp.read()

    return toml.loads(contents)


def find_file_by_options(
    path: pathlib.Path, options: Sequence[str]
) -> pathlib.Path | None:
    for option in options:
        option = path / option
        if option.exists():
            return option.resolve()

    return None


def pick_file(
    dest: dict[str, Any], key: str, path: pathlib.Path, options: Sequence[str]
) -> str | None:
    option = find_file_by_options(path, options)
    if option is not None:
        dest[key] = option.name
    else:
        dest.pop(key)
    return option.name


def set_if_available(
    dest: dict[str, Any],
    dest_key: str,
    source: dict[str, Any],
    source_key: str | None = None,
    use_default: bool = True,
) -> Any | None:
    source_key = source_key or dest_key
    if source_key in source:
        dest[dest_key] = source[source_key]
        return dest[dest_key]

    if not use_default:
        dest.pop(dest_key)
    return None


def convert_entrypoint(entrypoint: list[str]) -> dict[str, str]:
    def split(item: str) -> tuple[str, str]:
        key, value = item.split("=", 1)
        return key.strip(), value.strip()
    return dict(split(item) for item in entrypoint)


def convert_entrypoints(
    project: dict[str, Any],
    console_scripts: list[str] | None = None,
    gui_scripts: list[str] | None = None,
    **others: list[str],
) -> Any | None:
    if console_scripts:
        project["scripts"] = convert_entrypoint(console_scripts)
    else:
        project.pop("scripts", None)
    if gui_scripts:
        project["gui-scripts"] = convert_entrypoint(gui_scripts)
    if others:
        project["entry-points"] = {
            key.strip(): convert_entrypoint(entrypoint) for key, entrypoint in others.items()
        }


def convert_to_pyproject_toml(
    project_path: pathlib.Path,
    setup_kwargs: dict[str, Any],
):
    pyproject = get_pyproject_template()
    project = pyproject["project"]
    tool = pyproject["tool"]
    pick_file(
        tool["setuptools"]["dynamic"]["readme"],
        "file",
        project_path,
        ("README.md", "README.rst"),
    )
    pick_file(
        project["license"],
        "file",
        project_path,
        ("LICENSE.md", "LICENSE.txt", "LICENSE.rst", "LICENSE"),
    )
    set_if_available(project, "name", setup_kwargs)
    set_if_available(project, "description", setup_kwargs)
    set_if_available(project, "python_requires", setup_kwargs, "requires-python")
    set_if_available(project, "classifiers", setup_kwargs)
    set_if_available(project, "keywords", setup_kwargs)

    import_name = project["name"].replace("-", "_")

    # find_packages() replacement:
    tool["setuptools"]["packages"]["find"]["include"] = [import_name + '*']

    # Throw away:
    # packages
    # include_package_data
    convert_entrypoints(project, **setup_kwargs.get("entry_points", {}))

    dev_requirements = find_file_by_options(
        project_path,
        ("dev-requirements.txt", "requirements-dev.txt"),
    )
    doc_requirements = find_file_by_options(
        project_path,
        ("docs-requirements.txt", "requirements-docs.txt"),
    )
    if dev_requirements or doc_requirements:
        project["dynamic"].append("optional-dependencies")
        optional_deps = tool["setuptools"]["dynamic"]["optional-dependencies"]
        if dev_requirements:
            optional_deps["test"] = {"file": dev_requirements.name}
        if doc_requirements:
            optional_deps["doc"] = {"file": doc_requirements.name}

    pyproject["tool"]["setuptools_scm"]["write_to"] = f"{import_name}/_version.py"
    return pyproject


def migrate(path_to_repository: pathlib.Path):

    setup_py = find_file_by_options(path_to_repository, ("setup.py", "_setup.py"))
    if setup_py is None:
        print("No setup.py found", file=sys.stderr)
        sys.exit(1)

    with open(setup_py) as fp:
        source = fp.read()

    os.chdir(setup_py.parent)

    globals_ = dict(globals())
    globals_["__file__"] = str(setup_py)
    exec(source, globals_)

    setup_kwargs = setuptools.get_last_setup_kwargs()
    # import pprint; pprint.pprint(setup_kwargs)
    pyproject = convert_to_pyproject_toml(path_to_repository, setup_kwargs)

    setup_cfg = find_file_by_options(path_to_repository, ("setup.cfg",))
    if setup_cfg is not None:
        config = configparser.ConfigParser()
        config.read(setup_cfg)

        for section, section_data in config.items():
            if section == "DEFAULT":
                continue

            pyproject["tool"][section] = dict(section_data)

    pyproject["tool"].pop("versioneer", None)
    return pyproject


def main(path_to_repository: pathlib.Path):
    pyproject = migrate(path_to_repository)
    print(toml.dumps(pyproject))


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]))
