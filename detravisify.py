#!/usr/bin/env python3
"""
Travis configuration to a standalone script
"""
from __future__ import annotations

import argparse
import pathlib
import string
from dataclasses import dataclass, field
from typing import Any, Optional, Union

import apischema
import yaml

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent


class Script(str):
    ...


def _empty_script() -> Script:
    return Script("")


@apischema.deserializer
def _(value: Union[str, list[str]]) -> Script:
    """
    Scripts can be either strings or list of strings.

    We make a custom apischema deserializer here to make them both into a
    standard "Script" string.  For our purposes, it doesn't really matter that
    this loses the granularity of the job steps.
    """
    if not isinstance(value, str):
        value = "\n".join(value)

    return Script(value)


def env_to_dict(env_list: EnvironmentVariables) -> dict[str, str]:
    """
    Take an EnvironmentVariables instance and make a dictionary out of it.

    This is a bit more complicated than you'd expect, because it can be
    a string with VAR=VALUE, a dictionary or a list of dictionaries, or...

    Parameters
    ----------
    env_list : EnvironmentVariables

    Returns
    -------
    dict[str, str]
    """
    res = {}
    if isinstance(env_list, dict):
        env_list = [env_list]
    for env in env_list:
        if isinstance(env, str):
            env = dict([env.split("=", 1)])
        for var, value in env.items():
            value = str(value).removeprefix('"').removesuffix('"')
            res[var] = value
    return res


def env_to_exports(env_list: EnvironmentVariables) -> list[str]:
    """
    Convert EnvironmentVariables to sh-compatible ``export VAR=VALUE``.

    Parameters
    ----------
    env_list : EnvironmentVariables


    Returns
    -------
    list[str]

    """
    return [f'export {var}="{value}"' for var, value in env_to_dict(env_list).items()]


@dataclass
class Workspace:
    create: dict[str, Any] = field(default_factory=dict)
    use: Union[str, list[str]] = ""


EnvironmentVariable = Union[
    str,
    dict[str, Union[str, int, float]],
]
EnvironmentVariables = list[EnvironmentVariable]


@dataclass
class Environment:
    """
    Environment settings in a .travis.yml configuration.
    """

    global_: EnvironmentVariables = field(
        default_factory=list, metadata=apischema.metadata.alias(arg="global")
    )

    def to_script(self) -> str:
        if not self.global_:
            return ""
        return "\n".join(env_to_exports(self.global_))


@dataclass
class Job:
    """
    Job settings in a .travis.yml configuration.
    """

    stage: str = ""
    name: str = ""
    python: Union[float, str] = ""
    env: EnvironmentVariables = field(default_factory=list)
    workspaces: Optional[Workspace] = None
    before_install: Script = field(default_factory=_empty_script)
    install: Script = field(default_factory=_empty_script)
    before_script: Script = field(default_factory=_empty_script)
    script: Script = field(default_factory=_empty_script)

    before_deploy: Script = field(default_factory=_empty_script)
    # TODO: apischema bug?
    # on: tags: true becomes -> True: {'tags': True}}
    deploy: Optional[dict] = None
    after_deploy: Script = field(default_factory=_empty_script)

    after_script: Script = field(default_factory=_empty_script)
    after_success: Script = field(default_factory=_empty_script)
    after_failure: Script = field(default_factory=_empty_script)
    if_: str = field(default="", metadata=apischema.metadata.alias(arg="if"))

    def to_script(self) -> str:
        result = [
            f"# Job: {self.name} (stage: {self.stage})",
        ]

        def add_if_set(desc: str, lines: str):
            if not lines:
                return
            if result:
                result.append("")
            result.append(f"# {desc}")
            if lines in ("skip",):
                result.append("# (Skipped)")
            else:
                result.extend(lines.splitlines())

        if self.env:
            add_if_set("Environment settings:", "\n".join(env_to_exports(self.env)))

        # Ref: https://docs.travis-ci.com/user/job-lifecycle/
        add_if_set("Before install:", self.before_install)
        add_if_set("Install:", self.install)

        add_if_set("Before script:", self.before_script)
        add_if_set("Script:", self.script)

        add_if_set("Before deploy:", self.before_deploy)
        if self.deploy is not None:
            add_if_set("Deploy:", str(self.deploy))
        add_if_set("After deploy:", self.after_deploy)

        add_if_set("After script:", self.after_script)
        add_if_set("After success:", self.after_success)
        add_if_set("After failure:", self.after_failure)
        return "\n".join(result)


@dataclass
class Jobs:
    """
    Jobs listing in a .travis.yml configuration.
    """

    include: list[Job] = field(default_factory=list)
    exclude: list[Job] = field(default_factory=list)
    allow_failures: Union[bool, list[dict[str, str]]] = False
    fast_finish: bool = False

    def to_script(self) -> str:
        return "\n\n".join(include.to_script() for include in self.include)


def travis_yaml_to_bash(contents: str) -> str:
    """
    Convert Travis CI yaml source ``contents`` to a best-effort bash script.

    Parameters
    ----------
    contents : str
        Contents of the Travis CI yaml file (``.travis.yml``)

    Returns
    -------
    str
    """
    conf = yaml.load(contents, Loader=yaml.Loader)
    jobs = apischema.deserialize(Jobs, conf.get("jobs", {}))
    env = apischema.deserialize(Environment, conf.get("env", {}))

    env_script = env.to_script()
    result = []

    if env_script:
        result.append(env_script)
    for job in jobs.include:
        result.append(job.to_script())

    return "\n".join(result)


def dump_travis_to_bash(filename: str) -> None:
    """
    Dump converted Travis CI yaml source ``contents`` as a best-effort bash
    script.

    Parameters
    ----------
    filename : str

    """
    with open(filename, "rt") as fp:
        contents = fp.read()

    bash_source = travis_yaml_to_bash(contents)
    print(bash_source)


def split_extras(extras: str, remove: list[str]) -> list[str]:
    """
    Split (e.g.) PIP_EXTRAS into a list of package name requirements.

    Parameters
    ----------
    extras : str
        The Travis CI environment variable value.

    remove : list[str]
        Remove any of these packages, if found.

    Returns
    -------
    list[str]

    """
    all_extras = set(extra for extra in extras.strip().split())
    return sorted(all_extras - set(remove or []))


def simplify_extras(conda_extras: str, pip_extras: str) -> tuple[str, str, str]:
    """
    Simplify and clean up conda and pip extras.

    If a dependency is listed in both conda and pip extras, it will be moved
    to the shared "testing_extras" setting.

    Parameters
    ----------
    conda_extras : str
        Conda testing extras.
    pip_extras : str
        Pip testing extras.

    Returns
    -------
    str
        Shared testing extras
    str
        Conda testing extras
    str
        Pip testing extras
    """
    if not conda_extras or not pip_extras:
        return "", conda_extras, pip_extras

    if "-e ./" in pip_extras:
        pip_extras = pip_extras.replace("-e ./", "")

    conda_packages = split_extras(conda_extras, remove=["pip"])
    pip_packages = split_extras(pip_extras, remove=["-e", ".", "./"])
    common = set(conda_packages).intersection(pip_packages)
    conda_packages = sorted(set(conda_packages) - common)
    pip_packages = sorted(set(pip_packages) - common)
    return (" ".join(common), " ".join(conda_packages), " ".join(pip_packages))


def travis_yaml_to_pcds_python_gha(contents: str, template: str = "") -> str:
    """
    Convert Travis CI yaml source ``contents`` to a best-effort PCDS GHA Workflow.

    Parameters
    ----------
    contents : str
        Contents of the Travis CI yaml file (``.travis.yml``)

    template : str
        Template filename for GitHub Actions workflow.
        Defaults to ``python_gha_template.yml`` distributed in this repository.

    Returns
    -------
    str
    """
    if not template:
        template = str(SCRIPT_DIR / "python_gha_template.yml")

    conf = yaml.load(contents, Loader=yaml.Loader)
    env = env_to_dict(apischema.deserialize(Environment, conf.get("env", {})).global_)

    defaults = {
        "package_name": "?",
        "testing_extras": "",
        "CONDA_EXTRAS": "",
        "PIP_EXTRAS": "",
    }
    for key, default in defaults.items():
        env.setdefault(key, default)

    if "CONDA_PACKAGE" in env:
        env["package_name"] = env["CONDA_PACKAGE"]

    conda_extras = env["CONDA_EXTRAS"]
    pip_extras = env["PIP_EXTRAS"]
    env["testing_extras"], env["conda_extras"], env["pip_extras"] = simplify_extras(
        conda_extras, pip_extras
    )

    with open(template, "rt") as fp:
        tpl = string.Template(fp.read())
    return tpl.substitute(**env)


def travis_yaml_to_pcds_twincat_gha(contents: str, template: str = "") -> str:
    """
    Convert Travis CI yaml source ``contents`` to a best-effort PCDS GHA Workflow.

    For PCDS TwinCAT repositories.

    Parameters
    ----------
    contents : str
        Contents of the Travis CI yaml file (``.travis.yml``)

    template : str
        Template filename for the GitHub Actions workflow.
        Defaults to ``twincat_gha_template.yml`` distributed in this repository.

    Returns
    -------
    str
    """
    if not template:
        template = str(SCRIPT_DIR / "twincat_gha_template.yml")

    conf = yaml.load(contents, Loader=yaml.Loader)
    env = env_to_dict(apischema.deserialize(Environment, conf.get("env", {})).global_)

    defaults = {
        "package_name": "",
        "TWINCAT_STYLE_EXCLUDE": "",
    }
    for key, default in defaults.items():
        env.setdefault(key, default)

    with open(template, "rt") as fp:
        tpl = string.Template(fp.read())
    return tpl.substitute(**env)


def migrate_travis_to_gha(filename: str, template: str = ""):
    """
    Converted Travis CI yaml ``filename`` to GitHub Actions.

    Parameters
    ----------
    filename : str
        The
    template : str, optional
        A specific template filename to use.
    """
    with open(filename, "rt") as fp:
        contents = fp.read()

    if "travis/shared_configs/twincat" in contents:
        return travis_yaml_to_pcds_twincat_gha(contents, template=template)
    return travis_yaml_to_pcds_python_gha(contents, template=template)


def dump_travis_to_gha(filename: str, template: str):
    """
    Converted Travis CI yaml ``filename`` to GitHub Actions and output the
    workflow.

    Parameters
    ----------
    filename : str
        The
    template : str
        A specific template filename to use.
    """
    gha = migrate_travis_to_gha(filename, template)
    print(gha.rstrip())


def _create_argparser() -> argparse.ArgumentParser:
    """
    Create an ArgumentParser for detravisify.

    Returns
    -------
    argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser()

    subparsers = parser.add_subparsers(help="Commands")
    dump_script_parser = subparsers.add_parser(
        "dump", help="Dump script contents to a shell script"
    )
    dump_script_parser.add_argument("filename", type=str)
    dump_script_parser.set_defaults(func=dump_travis_to_bash)

    dump_script_parser = subparsers.add_parser(
        "gha", help="Convert script to PCDS-standard GitHub Actions"
    )
    dump_script_parser.add_argument("filename", type=str)
    dump_script_parser.add_argument("--template", default="", type=str)
    dump_script_parser.set_defaults(func=dump_travis_to_gha)
    return parser


def _main(args=None):
    """CLI entrypoint."""
    parser = _create_argparser()
    args = vars(parser.parse_args(args=args))
    func = args.pop("func", None)
    if func is None:
        parser.print_help()
        return

    return func(**args)


if __name__ == "__main__":
    _main()
