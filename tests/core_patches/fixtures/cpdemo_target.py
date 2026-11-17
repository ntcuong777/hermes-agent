"""Fixture module for the core-patch registry tests (a stand-in for an upstream-owned module)."""


def greet(name: str) -> str:
    """Say hello."""
    return f"hello {name}"


async def agreet(name: str) -> str:
    return f"hello {name}"


class Box:
    def size(self) -> int:
        return 1

    @staticmethod
    def make() -> "Box":
        return Box()


class Sub(Box):
    pass
