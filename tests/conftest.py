import pytest

from nbi import stack


@pytest.fixture(scope="session", autouse=True)
def _stack():
    stack("start")
    yield
    stack("stop")
