# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import importlib.util
import os
from types import ModuleType

SCRIPTS_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def load_script(name: str) -> ModuleType:
    """Import a helper from .github/scripts/, which is not a package on sys.path."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
