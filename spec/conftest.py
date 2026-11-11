"""Shared setup for the spec property tests.

Puts the bot's modules on the import path and registers Hypothesis profiles.
CI and spec/run_mutants.sh load the "ci" profile (HYPOTHESIS_PROFILE=ci): a
fixed search order and no example database, so every run checks the same inputs.
"""
import os
import sys

from hypothesis import settings

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

settings.register_profile("ci", derandomize=True, database=None, deadline=None, print_blob=True)
settings.register_profile("dev", deadline=None)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))
