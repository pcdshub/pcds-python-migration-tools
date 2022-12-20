import configparser
import copy
import os
import pathlib
import sys
from types import ModuleType
from typing import Any, Dict, List, Optional, Sequence

import setuptools as _setuptools
import toml


class SetuptoolsStandin(ModuleType):
    _setup_result = None

    def get_last_setup_kwargs(self) -> Optional[Dict[str, Any]]:
        return self._setup_result

    def find_packages(self, *args, **kwargs):
        return _setuptools.find_packages(*args, **kwargs)

    def setup(self, **kwargs):
        self._setup_result = kwargs

    def __getattr__(self, name: str) -> Any:
        return getattr(_setuptools, name)


setuptools = SetuptoolsStandin(name="setuptools")
sys.modules["setuptools"] = setuptools


_template = {
    "build-system": {
        "build-backend": "setuptools.build_meta",
        "requires": ["setuptools", "versioneer[toml]"],
    },
    "project": {
        "authors": [{"name": "SLAC National Accelerator Laboratory"}],
        "classifiers": ["Programming Language :: Python :: 3"],
        "description": "",
        "dynamic": ["version", "readme", "dependencies"],
        "keywords": [],
        "license": {"file": "LICENSE.md"},
        "name": "",
        "requires-python": ">=3.9",
        "scripts": {},  # "pmpsdb": "pmpsdb_client:cli.entrypoint"},
    },
    "tool": {
        "setuptools": {
            "packages": {
                "find": {
                    "where": [],
                    "include": [],
                    "namespaces": False,
                }
            },
            "dynamic": {
                "readme": {"file": ["README.md"]},
                "dependencies": {"file": ["requirements.txt"]},
                "optional-dependencies": {},
            },
        }
    },
}


def find_file_by_options(
    path: pathlib.Path, options: Sequence[str]
) -> Optional[pathlib.Path]:
    for option in options:
        option = path / option
        if option.exists():
            return option

    return None


def pick_file(
    dest: Dict[str, Any], key: str, path: pathlib.Path, options: Sequence[str]
) -> Optional[str]:
    option = find_file_by_options(path, options)
    if option is not None:
        dest[key] = option.name
    else:
        dest.pop(key)
    return option.name


def set_if_available(
    dest: Dict[str, Any],
    dest_key: str,
    source: Dict[str, Any],
    source_key: Optional[str] = None,
    use_default: bool = True,
) -> Optional[Any]:
    source_key = source_key or dest_key
    if source_key in source:
        dest[dest_key] = source[source_key]
        return dest[dest_key]

    if not use_default:
        dest.pop(dest_key)
    return None


def convert_entrypoint(entrypoint: List[str]) -> Dict[str, str]:
    return dict(item.split("=", 1) for item in entrypoint)


def convert_entrypoints(
    project: Dict[str, Any],
    console_scripts: Optional[List[str]] = None,
    gui_scripts: Optional[List[str]] = None,
    **others: List[str],
) -> Optional[Any]:
    if console_scripts:
        project["scripts"] = convert_entrypoint(console_scripts)
    if gui_scripts:
        project["gui-scripts"] = convert_entrypoint(gui_scripts)
    if others:
        project["entry-points"] = {
            key: convert_entrypoint(entrypoint) for key, entrypoint in others.items()
        }


def convert_to_pyproject_toml(
    project_path: pathlib.Path,
    setup_kwargs: Dict[str, Any],
):
    pyproject = copy.deepcopy(_template)
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

    # find_packages() replacement:
    tool["setuptools"]["packages"]["find"]["where"] = [project["name"]]

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

    return pyproject


def migrate(path_to_repository: pathlib.Path):
    setup_py = find_file_by_options(path_to_repository, ("setup.py", "_setup.py"))
    if setup_py is None:
        print("No setup.py found", file=sys.stderr)
        sys.exit(1)

    with open(setup_py, "rt") as fp:
        source = fp.read()

    os.chdir(setup_py.parent)
    exec(source)

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

    return pyproject


def main(path_to_repository: pathlib.Path):
    pyproject = migrate(path_to_repository)
    print(toml.dumps(pyproject))


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]))
