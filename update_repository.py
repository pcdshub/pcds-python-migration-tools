#!/usr/bin/env python

import dataclasses
import pathlib
import sys
import textwrap

from detravisify import migrate_travis_to_gha


@dataclasses.dataclass
class Repository:
    root: pathlib.Path


@dataclasses.dataclass
class Fix:
    name: str
    repo: Repository

    @property
    def description(self) -> str:
        return "?"

    def run(self):
        ...

    def __repr__(self):
        return f"{self.name}: {self.description}"


@dataclasses.dataclass(repr=False)
class GitHubActionsMigration(Fix):
    workflow: str = ""

    def __post_init__(self):
        self.workflow = migrate_travis_to_gha(str(self.travis_yml)).rstrip()

    @property
    def description(self) -> str:
        return (
            f"Migrate to GitHub Actions and delete .travis.yml:\n"
            f"{self.workflow}"
        )

    @property
    def travis_yml(self) -> pathlib.Path:
        return self.repo.root / ".travis.yml"

    def run(self):
        workflows = self.repo.root / ".github" / "workflows"
        workflows.mkdir(parents=True, exist_ok=True)

        with open(workflows / "standard.yml", "wt") as fp:
            print(self.workflow, file=fp)

        if self.travis_yml.exists():
            self.travis_yml.unlink()


# @dataclasses.dataclass(repr=False)
# class UpdateSphinxConfig(Fix):
#   year, extensions, ...


@dataclasses.dataclass(repr=False)
class DeleteFiles(Fix):
    files: list[pathlib.Path]

    @property
    def description(self) -> str:
        return f"Delete these files: {self.files}"

    def run(self):
        for file in self.files:
            file.unlink()


# @dataclasses.dataclass(repr=False)
# class CopyFiles(Fix):
#   copy updated files from cookiecutter

def get_fixes(root: pathlib.Path) -> list[Fix]:
    """
    Update a repository to the latest standards.
    """

    root = root.expanduser().resolve()
    repo = Repository(root=root)
    if not (root / ".git").exists():
        raise RuntimeError("Not a repository root?")

    fixes = []
    to_remove = [
        root / "run_tests.py",
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

    travis = root / ".travis.yml"
    if travis.exists():
        fixes.append(
            GitHubActionsMigration(
                name="gha",
                repo=repo,
            )
        )

    return fixes


def main(path: str, dry_run: bool = True):
    root = pathlib.Path(path)
    fixes = get_fixes(root)
    for fix in fixes:
        desc = textwrap.indent(str(fix), prefix="    ")
        print(f"* {desc.lstrip()}")

    if not dry_run:
        for fix in fixes:
            fix.run()


if __name__ == "__main__":
    main(sys.argv[1])
