# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Build hook: bundle the starter content into the wheel as agenteval/_starter/.

All project metadata lives in pyproject.toml. The repository keeps rules/, exams/,
certification/ and examples/ at the top level for contributors; this hook copies them
into the built package so `pip install agenteval-toolkit` works out of the box.
"""

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

STARTER_DIRS = ("rules", "exams", "certification", "examples")


class BuildWithStarter(build_py):
    def run(self):
        super().run()
        root = Path(__file__).resolve().parent
        dest = Path(self.build_lib) / "agenteval" / "_starter"
        for name in STARTER_DIRS:
            src = root / name
            if src.is_dir():
                shutil.copytree(src, dest / name, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


setup(cmdclass={"build_py": BuildWithStarter})
