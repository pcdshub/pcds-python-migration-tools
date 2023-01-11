#!/usr/bin/env python

import argparse
import dataclasses
import difflib
import logging
import pathlib
import subprocess
import textwrap
from typing import Any, Optional, Union

import jinja2

from detravisify import migrate_travis_to_gha

logger = logging.getLogger(__name__)

script_path = pathlib.Path(__file__).parent.resolve()
pyupgrade_skip_files = [
    "setup.py",
    "versioneer.py",
    "_version.py",
]
cookiecutter_root = script_path / "cookiecutter-pcds-python" / "{{ cookiecutter.folder_name }}"


@dataclasses.dataclass
class Repository:
    root: pathlib.Path
    template_defaults: dict[str, Any]

    def run_command(self, command: list[str]):
        print("Running:", " ".join(command))
        subprocess.check_call(
            command,
            # shell=True,
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
    project_name: str = "project-name"   # pcdshub/project-name
    repo_name: str = "project_name"      # import project_name
    import_name: str = ""                # import project_name
    folder_name: str = ""


@dataclasses.dataclass
class Fix:
    name: str
    repo: Repository
    files: list[pathlib.Path] = dataclasses.field(default_factory=list, init=False)

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
    def title(self) -> str:
        raise NotImplementedError()

    @property
    def description(self) -> str:
        desc = [self.title]
        for idx, fix in enumerate(self.nested_fixes, 1):
            sub_fix_desc = textwrap.indent(fix.description, prefix="    ")
            desc.append(
                f"{idx}. {sub_fix_desc}"
            )
        return "\n".join(desc)

    def run(self):
        for fix in self.nested_fixes:
            fix.run()


@dataclasses.dataclass(repr=False)
class GitHubActionsMigration(NestedFix):
    workflow: str = ""

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

    @property
    def title(self) -> str:
        return "Migrate to GitHub Actions"


@dataclasses.dataclass(repr=False)
class UpdateSphinxConfig(Fix):
    file: pathlib.Path
    original_contents: str = dataclasses.field(init=False)
    new_contents: str = dataclasses.field(init=False)
    diff: str = dataclasses.field(init=False)
    # year, extensions, ...

    def __post_init__(self):
        with open(self.file, "rt") as fp:
            self.original_contents = fp.read()

        self.new_contents = self.original_contents

        self.new_contents = self.new_contents.replace("doctr_versions_menu", "docs-versions-menu")
        self.new_contents = self.new_contents.replace("language = None", 'language = "en"')

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
            fp.write(self.new_contents)


@dataclasses.dataclass(repr=False)
class AddFile(Fix):
    file: pathlib.Path
    contents: str = ""
    mode: str = "wt"

    def __post_init__(self):
        self.files = [self.file]

    @property
    def description(self) -> str:
        if "t" in self.mode:
            return f"Add {self.file} with contents:\n{self.contents}"
        return f"Add binary {self.file}"

    def run(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file, self.mode) as fp:
            fp.write(self.contents)

        self.repo.run_command(["git", "add", str(self.file)])


@dataclasses.dataclass
class TemplateFile:
    template_file: Union[str, pathlib.Path]
    possible_files: list[Union[str, pathlib.Path]] = dataclasses.field(default_factory=list)
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
            fp.write(self.contents)

        self.repo.run_command(["git", "add", str(self.dest_file)])


@dataclasses.dataclass(repr=False)
class DeleteFiles(Fix):
    files: list[pathlib.Path] = dataclasses.field(default_factory=list)

    @property
    def description(self) -> str:
        return f"Delete these files: {self.files}"

    def run(self):
        for file in self.files:
            file.unlink()


@dataclasses.dataclass(repr=False)
class RunPyupgrade(Fix):
    skip_files: list[str] = dataclasses.field(default_factory=list)

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
        pyupgrade_main(argv=[*python_source_files, "--py39-plus"])


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
                DeleteFiles(
                    name=f"remove-{file.name}",
                    repo=repo,
                    files=[file]
                )
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

    fixes.append(GitCommit(name="commit", repo=repo, message="MNT: update repository with pcds-migration-tools"))

    sphinx_config = repo.root / "docs" / "source" / "conf.py"
    if sphinx_config.exists():
        sphinx_update = UpdateSphinxConfig(name="update_sphinx_config", repo=repo, file=sphinx_config)
        if sphinx_update.changed:
            fixes.append(sphinx_update)
            fixes.append(GitCommit(name="commit", repo=repo, message="DOC: update sphinx config with pcds-migration-tools"))

    fixes.append(RunPyupgrade("pyupgrade", repo, skip_files=pyupgrade_skip_files))
    fixes.append(GitCommit(name="commit", repo=repo, message="STY: update repository to Python 3.9+ standards"))
    return fixes


def run_fixes(
    fixes: list[Fix],
    dry_run: bool = True,
    skip: Optional[list[str]] = None,
):
    skip = skip or []
    for fix in fixes:
        if fix.name in skip:
            continue

        desc = textwrap.indent(str(fix), prefix="    ")
        print(f"{desc.lstrip()}")
        print()

    if not dry_run:
        for fix in fixes:
            if fix.name in skip:
                continue

            try:
                fix.run()
            except Exception:
                logger.error("Failed to run fix: %s", str(fix), exc_info=True)
                logger.error("Continue? [Y/n]")
                if input().lower() not in ("", "y", "yes"):
                    break


def main(repo_root: str, dry_run: bool = True):
    root = pathlib.Path(repo_root).expanduser().resolve()
    repo = Repository(
        root=root,
        template_defaults=get_template_defaults(root),
    )

    fixes = get_fixes(repo)
    return run_fixes(fixes, dry_run=dry_run)


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
    return parser


def _main(args=None):
    """CLI entrypoint."""
    parser = _create_argparser()
    return main(**vars(parser.parse_args(args=args)))


if __name__ == "__main__":
    _main()
