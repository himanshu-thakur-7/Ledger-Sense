"""Pytest configuration shared by all future test suites.

W0 ships no tests of its own — its acceptance criterion is that `pytest`
collects zero tests and exits 0. Pytest's default exit code for "no tests
collected" is 5, which would otherwise fail CI on an empty (but valid)
repo skeleton. Once later cards add real tests, this hook is a no-op.
"""

from _pytest.config import ExitCode


def pytest_sessionfinish(session, exitstatus):
    if exitstatus == ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = ExitCode.OK
