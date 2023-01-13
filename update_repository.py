#!/usr/bin/env python

from __future__ import annotations

import argparse
import dataclasses
import difflib
import enum
import logging
import pathlib
import subprocess
import textwrap
from typing import Any, Optional, Union

import jinja2
import toml

from detravisify import migrate_travis_to_gha
from setup_to_pyproject import migrate as migrate_setup_py_to_pyproject

logger = logging.getLogger(__name__)

script_path = pathlib.Path(__file__).parent.resolve()
pyupgrade_skip_files = [
    "setup.py",
    "versioneer.py",
    "_version.py",
]
cookiecutter_root = (
    script_path / "cookiecutter-pcds-python" / "{{ cookiecutter.folder_name }}"
)
cookiecutter_import_path = cookiecutter_root / "{{ cookiecutter.import_name }}"


class Fixes(str, enum.Enum):
    pyproject_toml = "pyproject.toml"
    setuptools_scm = "setuptools_scm"

    def __str__(self):
        return self.value


@dataclasses.dataclass
class Repository:
    root: pathlib.Path
    template_defaults: dict[str, Any]
    import_name: str = ""
    python_version: str = "3.9"

    @property
    def import_dir(self) -> pathlib.Path:
        # TODO import name in Repository instance
        return self.root / get_repo_name(self.root)

    def run_command(self, command: list[str]) -> bool:
        print("Running:", " ".join(command))
        return not subprocess.check_call(
            command,
            cwd=self.root,
        )

    def run_command_with_output(self, command: list[str]) -> bytes:
        print("Running:", " ".join(command))
        return subprocess.check_output(
            command,
            cwd=self.root,
        )


@dataclasses.dataclass
class CookiecutterSettings:
    author_name: str = "SLAC National Accelerator Laboratory"
    auto_git_setup: str = "no"
    git_remote_name: str = "origin"
    github_repo_group: str = "pcdshub"
    python_interpreter: str = "python3"

    # Don't care (at the moment) about these:
    description: str = ""
    email: str = ""

    # To determine:
    project_name: str = "project-name"  # pcdshub/project-name
    repo_name: str = "project_name"  # import project_name
    import_name: str = ""  # import project_name
    folder_name: str = ""


@dataclasses.dataclass
class Fix:
    name: str
    repo: Repository
    files: list[pathlib.Path] = dataclasses.field(default_factory=list, init=False)

    @property
    def commit_message(self) -> str:
        return ""

    @property
    def description(self) -> str:
        raise NotImplementedError()

    def run(self):
        ...

    def __str__(self):
        return f"## {self.name}: {self.description}"


class NestedFix(Fix):
    nested_fixes: list[Fix] = dataclasses.field(default_factory=list, init=False)

    @property
    def commit_message(self) -> str:
        raise NotImplementedError()

    @property
    def description(self) -> str:
        desc = [self.commit_message]
        for idx, fix in enumerate(self.nested_fixes, 1):
            sub_fix_desc = textwrap.indent(fix.description, prefix="    ")
            desc.append(f"{idx}. {sub_fix_desc}")
        return "\n".join(desc)

    def run(self):
        for fix in self.nested_fixes:
            fix.run()


@dataclasses.dataclass(repr=False)
class GitHubActionsMigration(NestedFix):
    workflow: str = ""

    @property
    def commit_message(self) -> str:
        return "CI: migrate to GitHub actions"

    def __post_init__(self):
        travis_yml = self.repo.root / ".travis.yml"
        self.workflow = migrate_travis_to_gha(str(travis_yml)).rstrip()

        if not travis_yml.exists():
            raise RuntimeError("Repositories without CI not yet supported")

        self.nested_fixes = [
            DeleteFiles(
                name="delete_travis",
                repo=self.repo,
                files=[travis_yml],
            ),
            AddFile(
                name="add_gha_workflow",
                repo=self.repo,
                file=self.repo.root / ".github" / "workflows" / "standard.yml",
                contents=self.workflow,
            ),
        ]


@dataclasses.dataclass(repr=False)
class UpdateSphinxConfig(Fix):
    file: pathlib.Path
    original_contents: str = dataclasses.field(init=False)
    new_contents: str = dataclasses.field(init=False)
    diff: str = dataclasses.field(init=False)
    # year, extensions, ...

    @property
    def commit_message(self) -> str:
        return "DOC: update Sphinx configuration"

    def __post_init__(self):
        with open(self.file, "rt") as fp:
            self.original_contents = fp.read()

        self.new_contents = self.original_contents

        self.new_contents = self.new_contents.replace(
            "doctr_versions_menu", "docs-versions-menu"
        )
        self.new_contents = self.new_contents.replace(
            "language = None", 'language = "en"'
        )

        if "sphinxcontrib.jquery" not in self.new_contents:
            self.new_contents = self.new_contents.replace(
                "extensions = [",
                'extensions = [\n    "sphinxcontrib.jquery",\n',
            )

        diff = difflib.unified_diff(
            self.original_contents.splitlines(),
            self.new_contents.splitlines(),
            fromfile=str(self.file),
            tofile=str(self.file),
        )
        self.diff = textwrap.indent("\n".join(diff), "> ", predicate=lambda _: True)

    @property
    def changed(self) -> bool:
        return self.original_contents != self.new_contents

    @property
    def description(self) -> str:
        if self.changed:
            return f"Update {self.file}:\n{self.diff}"
        return "No change to sphinx config"

    def run(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file, "wt") as fp:
            print(self.new_contents.rstrip(), file=fp)


@dataclasses.dataclass(repr=False)
class PyprojectTomlMigration(NestedFix):
    pyproject_toml: str = dataclasses.field(init=False)

    @property
    def commit_message(self) -> str:
        return "BLD: migrate to pyproject.toml"

    def __post_init__(self):
        self.pyproject_toml = toml.dumps(migrate_setup_py_to_pyproject(self.repo.root))
        self.nested_fixes = [
            DeleteFiles(
                name="delete_setup_py",
                repo=self.repo,
                files=[self.repo.root / "setup.py", self.repo.root / "setup.cfg"],
                missing_ok=True,
            ),
            AddFile(
                name="add_pyproject_toml",
                repo=self.repo,
                file=self.repo.root / "pyproject.toml",
                contents=self.pyproject_toml,
            ),
        ]


@dataclasses.dataclass(repr=False)
class SetuptoolsScmMigration(NestedFix):
    @property
    def commit_message(self) -> str:
        return "BLD: migrate to setuptools-scm"

    def __post_init__(self):
        self.nested_fixes = [
            DeleteFiles(
                name="delete_versioneer",
                repo=self.repo,
                files=[
                    self.repo.root / "versioneer.py",
                    self.repo.import_dir / "_version.py",
                ],
                missing_ok=True,
            ),
            RemoveLines(
                name="versioneer_lines",
                repo=self.repo,
                file=self.repo.import_dir / "__init__.py",
                lines=[
                    "__version__ = _version.get_versions()['version']",
                    "__version__ = get_versions()['version']",
                    "del get_versions",
                    "del _version",
                    "from . import _version",
                    "from ._version import get_versions",
                ]
            ),
            PrependLines(
                name="setuptools_scm_version",
                repo=self.repo,
                file=self.repo.import_dir / "__init__.py",
                lines=[
                    "from .version import __version__  # noqa: F401",
                ]
            ),
            AddFileFromTemplate(
                "add_setuptools_version",
                template_file=pathlib.Path("version.py"),
                dest_file=self.repo.import_dir / "version.py",
                repo=self.repo,
                source_base_path=cookiecutter_import_path,
            ),
        ]


@dataclasses.dataclass(repr=False)
class AddFile(Fix):
    file: pathlib.Path
    contents: str = ""
    mode: str = "wt"

    def __post_init__(self):
        self.files = [self.file]

    @property
    def commit_message(self) -> str:
        return ""

    @property
    def description(self) -> str:
        if "t" in self.mode:
            return f"Add {self.file} with contents:\n{self.contents}"
        return f"Add binary {self.file}"

    def run(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file, self.mode) as fp:
            print(self.contents.rstrip(), file=fp)

        self.repo.run_command(["git", "add", str(self.file)])


@dataclasses.dataclass(repr=False)
class PrependLines(Fix):
    file: pathlib.Path
    lines: list[str]
    skip_if_present: bool = True

    @property
    def commit_message(self) -> str:
        return ""

    def __post_init__(self):
        self.files = [self.file]

    @property
    def description(self) -> str:
        return f"Prepend lines to {self.file}:\n{self.lines}"

    def run(self):
        with open(self.file) as fp:
            lines = fp.read().splitlines()

        for line in self.lines:
            if not self.skip_if_present or line not in lines:
                lines.insert(0, line)

        with open(self.file, "wt") as fp:
            print("\n".join(lines).rstrip(), file=fp)


@dataclasses.dataclass(repr=False)
class AppendLines(Fix):
    file: pathlib.Path
    lines: list[str]
    skip_if_present: bool = True

    @property
    def commit_message(self) -> str:
        return ""

    def __post_init__(self):
        self.files = [self.file]

    @property
    def description(self) -> str:
        return f"Add lines to {self.file}:\n{self.lines}"

    def run(self):
        with open(self.file) as fp:
            lines = fp.read().splitlines()

        for line in self.lines:
            if not self.skip_if_present or line not in lines:
                lines.append(line)

        with open(self.file, "wt") as fp:
            print("\n".join(lines).rstrip(), file=fp)


@dataclasses.dataclass(repr=False)
class RemoveLines(Fix):
    file: pathlib.Path
    lines: list[str]

    def __post_init__(self):
        self.files = [self.file]

    @property
    def commit_message(self) -> str:
        return ""

    @property
    def description(self) -> str:
        return f"Remove matching lines from {self.file}:\n{self.lines}"

    def run(self):
        with open(self.file) as fp:
            lines = fp.read().splitlines()

        for line in self.lines:
            while line in lines:
                lines.remove(line)

        with open(self.file, "wt") as fp:
            print("\n".join(lines).rstrip(), file=fp)


@dataclasses.dataclass
class TemplateFile:
    template_file: Union[str, pathlib.Path]
    possible_files: list[Union[str, pathlib.Path]] = dataclasses.field(
        default_factory=list
    )
    add_if_missing: bool = True

    def __post_init__(self):
        if not self.possible_files:
            self.possible_files = [self.template_file]


@dataclasses.dataclass(repr=False)
class AddFileFromTemplate(Fix):
    dest_file: pathlib.Path
    template_file: pathlib.Path
    source_base_path: pathlib.Path = cookiecutter_root
    template_args: dict[str, Any] = dataclasses.field(default_factory=dict)
    contents: str = dataclasses.field(init=False)
    existing_contents: Optional[str] = dataclasses.field(init=False)
    changed: bool = False
    mode: str = "wt"

    @property
    def commit_message(self) -> str:
        return f"MNT: updating {self.template_file.name} from template"

    def __post_init__(self):
        template_file = self.source_base_path / self.template_file

        with open(template_file, "rt") as fp:
            template = jinja2.Template(fp.read())

        template_args = dict(self.repo.template_defaults)
        template_args.update(self.template_args)

        self.contents = template.render(**template_args)

        target_file = self.repo.root / self.dest_file
        self.files = [target_file]
        if target_file.exists():
            with open(target_file, "rt") as fp:
                self.existing_contents = fp.read()

            self.changed = self.existing_contents != self.contents
        else:
            self.existing_contents = None

    @property
    def description(self) -> str:
        if self.existing_contents is None:
            return f"Add *new* {self.dest_file} from template with args {self.template_args}:\n{self.contents}"

        diff = difflib.unified_diff(
            self.existing_contents.splitlines(),
            self.contents.splitlines(),
            fromfile=f"template/{self.template_file}",
            tofile=str(self.repo.root / self.dest_file),
        )
        diff = textwrap.indent("\n".join(diff), "> ", predicate=lambda _: True)
        if self.changed:
            return f"Modify {self.dest_file} from template with args {self.template_args}:\n{diff}"
        return f"{self.dest_file} from template is unchanged; this fix is a no-op"

    def run(self):
        self.dest_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.dest_file, self.mode) as fp:
            print(self.contents.rstrip(), file=fp)

        self.repo.run_command(["git", "add", str(self.dest_file)])


@dataclasses.dataclass(repr=False)
class DeleteFiles(Fix):
    files: list[pathlib.Path] = dataclasses.field(default_factory=list)
    missing_ok: bool = False

    @property
    def commit_message(self) -> str:
        files = ", ".join(file.name for file in self.files)
        return f"CLN: removing {files}"

    @property
    def description(self) -> str:
        return f"Delete these files: {self.files}"

    def run(self):
        for file in self.files:
            try:
                file.unlink()
            except FileNotFoundError:
                if self.missing_ok:
                    continue
                raise


@dataclasses.dataclass(repr=False)
class RunPyupgrade(Fix):
    skip_files: list[str] = dataclasses.field(default_factory=list)

    @property
    def commit_message(self) -> str:
        return f"STY: update repository to Python {self.repo.python_version}+ standards"

    @property
    def description(self) -> str:
        return "Run pyupgrade"

    def run(self):
        try:
            from pyupgrade._main import main as pyupgrade_main
        except ImportError:
            logger.error("Unable to import and use pyupgrade", exc_info=True)
            return

        python_source_files = list(
            str(file)
            for file in self.repo.root.glob("**/*.py")
            if file.name not in self.skip_files
        )

        ver = self.repo.python_version.replace(".", "")
        pyupgrade_main(argv=[f"--py{ver}-plus", *python_source_files])


@dataclasses.dataclass
class GitCommit(Fix):
    message: str

    @property
    def description(self) -> str:
        return f"Create git commit with message: {self.message}"

    def run(self):
        self.repo.run_command(["git", "commit", "-an", "-m", self.message])


def get_repo_name(root: pathlib.Path) -> str:
    # TODO: better detection
    return root.name


def get_template_defaults(root: pathlib.Path) -> dict[str, Any]:
    cookiecutter = CookiecutterSettings()
    cookiecutter.project_name = get_repo_name(root)
    cookiecutter.repo_name = cookiecutter.project_name.replace("-", "_")
    cookiecutter.import_name = cookiecutter.repo_name
    return {
        "cookiecutter": cookiecutter,
    }


def get_fixes(repo: Repository) -> list[Fix]:
    """
    Update a repository to the latest standards.
    """
    if not (repo.root / ".git").exists():
        raise RuntimeError("Not a repository root?")

    fixes = []
    to_remove = [
        repo.root / "run_tests.py",
    ]
    for file in to_remove:
        if file.exists():
            fixes.append(
                DeleteFiles(name=f"remove-{file.name}", repo=repo, files=[file])
            )

    to_update = [
        TemplateFile(
            template_file="LICENSE",
            possible_files=["LICENSE", "LICENSE.md", "LICENSE.rst"],
        ),
        TemplateFile(
            template_file="AUTHORS.rst",
        ),
        TemplateFile(
            template_file="CONTRIBUTING.rst",
        ),
        TemplateFile(
            template_file=".pre-commit-config.yaml",
        ),
        TemplateFile(
            template_file=".flake8",
        ),
        TemplateFile(
            template_file=".coveragerc",
        ),
        TemplateFile(
            template_file=".git_archival.txt",
        ),
        TemplateFile(
            template_file=".gitattributes",
        ),
    ]

    for file in to_update:
        for dest_file in file.possible_files:
            if (repo.root / dest_file).exists():
                break
        else:
            if not file.add_if_missing:
                continue
            dest_file = file.template_file

        fixes.append(
            AddFileFromTemplate(
                f"add_{file.template_file}",
                template_file=pathlib.Path(file.template_file),
                dest_file=repo.root / dest_file,
                repo=repo,
                source_base_path=cookiecutter_root,
            )
        )

    travis = repo.root / ".travis.yml"
    if travis.exists():
        fixes.append(
            GitHubActionsMigration(
                name="gha",
                repo=repo,
            )
        )

    sphinx_config = repo.root / "docs" / "source" / "conf.py"
    if sphinx_config.exists():
        sphinx_update = UpdateSphinxConfig(
            name="update_sphinx_config", repo=repo, file=sphinx_config
        )
        if sphinx_update.changed:
            fixes.append(sphinx_update)

    fixes.append(RunPyupgrade("pyupgrade", repo, skip_files=pyupgrade_skip_files))

    setup_py = repo.root / "setup.py"
    if setup_py.exists():
        fixes.append(
            PyprojectTomlMigration(name=Fixes.pyproject_toml, repo=repo)
        )

    versioneer = repo.root / "versioneer.py"
    new_version_py = repo.import_dir / "version.py"
    if versioneer.exists() or not new_version_py.exists():
        fixes.append(
            SetuptoolsScmMigration(name=Fixes.setuptools_scm, repo=repo)
        )

    return fixes


def start_commit(fix: Fix) -> Optional[GitCommit]:
    commit = GitCommit(
        name="git_commit",
        repo=fix.repo,
        message=(
            f"{fix.commit_message}\n\n"
            f"Performed by pcds-migration-tools {fix.__class__.__name__}"
        ),
    )

    git_status = fix.repo.run_command_with_output(
        ["git", "status", "--porcelain"]
    )
    if not git_status:
        logger.warning("No changes to the repository in this step.")
        logger.warning("Skipping commit.")
        return None

    return commit


def run_fixes(
    fixes: list[Fix],
    dry_run: bool = True,
    skip: Optional[list[str]] = None,
    only: Optional[list[str]] = None,
):
    skip = skip or []
    only = only or []
    for fix in fixes:
        if fix.name in skip:
            print(f"({fix.name} skipped)")
            print()
            continue

        desc = textwrap.indent(str(fix), prefix="    ")
        print(f"{desc.lstrip()}")
        print()

    if dry_run:
        return

    print("\n" * 5)
    print("** Running fixes...")
    for fix in fixes:
        if only and fix.name not in only:
            continue
        if fix.name in skip:
            continue

        desc = textwrap.indent(str(fix), prefix="    ")
        print(f"{desc.lstrip()}")

        try:
            fix.run()
        except Exception:
            logger.error("Failed to run fix: %s", str(fix), exc_info=True)
            logger.error("Continue? [Y/n]")
            if input().lower() not in ("", "y", "yes"):
                break

        if not fix.commit_message:
            continue

        commit = start_commit(fix)
        if commit is None:
            continue

        try:
            commit.run()
        except Exception:
            logger.error(f"Failed to commit changes:\n{commit.message}")
            logger.error("Continue? [Y/n]")
            if input().lower() not in ("", "y", "yes"):
                break


def main(
    repo_root: str,
    dry_run: bool = True,
    python_version: str = "3.9",
    skip: Optional[list[str]] = None,
    only: Optional[list[str]] = None,
):
    root = pathlib.Path(repo_root).expanduser().resolve()
    repo = Repository(
        root=root,
        template_defaults=get_template_defaults(root),
        python_version=python_version,
    )

    fixes = get_fixes(repo)
    return run_fixes(fixes, dry_run=dry_run, skip=skip, only=only)


def _create_argparser() -> argparse.ArgumentParser:
    """
    Create an ArgumentParser for detravisify.

    Returns
    -------
    argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_root", type=str)
    parser.add_argument("--write", action="store_false", dest="dry_run")
    parser.add_argument("--python-version", type=str, default="3.9")
    parser.add_argument("--skip", type=str, action="append")
    parser.add_argument("--only", type=str, action="append")
    return parser


def _main(args=None):
    """CLI entrypoint."""
    parser = _create_argparser()
    return main(**vars(parser.parse_args(args=args)))


if __name__ == "__main__":
    _main()
