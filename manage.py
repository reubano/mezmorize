#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: sw=4:ts=4:expandtab
"""
manage.py
~~~~~~~~~

A script to manage development tasks
"""

from pathlib import Path
from subprocess import call, check_call, CalledProcessError
from mezmorize.utils import parse_verbosity
from os import environ

import click

BASEDIR = Path(__file__)
# DEF_WHERE = ["app", "manage.py", "config.py"]


@click.group()
@click.version_option()
@click.option(
    "-v",
    "--verbose",
    help="Specify multiple times to increase logging verbosity (overridden by -q)",
    count=True,
)
@click.option("-q", "--quiet", help="Only log errors (overrides -v)", is_flag=True)
@click.pass_context
def manager(ctx, verbose=0, quiet=False):
    """CLI manager."""
    verbose = ctx.params["verbose"]
    environ["VERBOSITY"] = parse_verbosity(verbose, quiet)


def upload_():
    """Upload distribution files"""
    upload_dir = BASEDIR.joinpath("dist", "*")
    url = "https://upload.pypi.org/legacy/"
    check_call(f"twine upload --repository-url {url} {upload_dir}", shell=True)


def build_():
    """Create a source distribution package"""
    check_call(BASEDIR.joinpath("helpers", "build"))


def clean_():
    """Remove Python file and build artifacts"""
    check_call(BASEDIR.joinpath("helpers", "clean"))


@manager.command()
def check():
    """Check staged changes for lint errors"""
    exit(call(BASEDIR.joinpath("helpers", "check-stage")))


@manager.command()
@click.option("-w", "--where", help="Modules to check")
@click.option("-f", "--fix", help="Fix errors", is_flag=True)
@click.option("-s", "--strict", help="Check with pylint", is_flag=True)
@click.option(
    "-p",
    "--parallel",
    help="Run linter in parallel in multiple processes",
    is_flag=True,
)
def lint(where=None, fix=False, strict=False, parallel=False):
    """Check style with linters"""
    args = "pylint --rcfile=tests/.pylintrc -rn mezmorize"
    args += " -j 0" if parallel else ""
    r_args = "ruff check --fix" if fix else "ruff check"
    r_args += f" {where}" if where else ""

    try:
        check_call(r_args.split(" "))
        check_call(args.split(" ")) if strict else None
    except CalledProcessError as e:
        exit(e.returncode)


@manager.command()
@click.option("-w", "--where", help="Modules to check")
def prettify(where):
    """Prettify code with black"""
    extra = where.split(" ") if where else []

    try:
        check_call(["ruff", "format"] + extra)
    except CalledProcessError as e:
        exit(e.returncode)


@manager.command()
def pipme():
    """Install requirements.txt"""
    exit(call("pip install -r requirements.txt".split(" ")))


@manager.command()
def require():
    """Create requirements.txt"""
    cmd = "pip freeze -l | grep -vxFf dev-requirements.txt > requirements.txt"
    exit(call(cmd, shell=True))


@manager.command()
@click.option("-s", "--source", help="the tests to run", default=None)
@click.option("-x", "--stop", help="Stop after first error", is_flag=True)
@click.option("-f", "--failed", help="Run failed tests", is_flag=True)
@click.option("-c", "--cover", help="Add coverage report", is_flag=True)
@click.option("-t", "--tox", help="Run tox tests", is_flag=True)
@click.option("-v", "--verbose", help="Use detailed errors", is_flag=True)
@click.option(
    "-p", "--parallel", help="Run tests in parallel in multiple processes", is_flag=True
)
def test(source=None, where=None, stop=False, **kwargs):
    """Run pytest, tox, and script tests"""
    opts = "-xv" if stop else "-v"
    opts += " --cov=mezmorize" if kwargs.get("cover") else ""
    opts += " --last-failed" if kwargs.get("failed") else ""
    opts += " --numprocesses=auto" if kwargs.get("parallel") else ""
    opts += " --tb=long -ra" if kwargs.get("verbose") else ""
    opts += f" {source}" if source else ""

    try:
        if kwargs.get("tox"):
            check_call(["tox", "-p"] if kwargs.get("parallel") else "tox")
        else:
            check_call(("pytest %s" % opts).split(" "))
    except CalledProcessError as e:
        exit(e.returncode)


@manager.command()
def register():
    """Register package with PyPI"""
    exit(call(["python", BASEDIR.joinpath("setup.py"), "register"]))


@manager.command()
def release():
    """Package and upload a release"""
    try:
        clean_()
        build_()
        upload_()
    except CalledProcessError as e:
        exit(e.returncode)


@manager.command()
def build():
    """Create a source distribution and wheel package"""
    try:
        clean_()
        build_()
    except CalledProcessError as e:
        exit(e.returncode)


@manager.command()
def upload():
    """Upload distribution files"""
    try:
        upload_()
    except CalledProcessError as e:
        exit(e.returncode)


@manager.command()
def clean():
    """Remove Python file and build artifacts"""
    try:
        clean_()
    except CalledProcessError as e:
        exit(e.returncode)


if __name__ == "__main__":
    manager()
