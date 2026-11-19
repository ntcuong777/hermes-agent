"""Fixture module for the safety-shell tests: a raising function and (async) generator targets."""


def fails(x: int) -> int:
    raise ValueError(f"original raised for {x}")


def count(n: int):
    for i in range(n):
        yield i


async def acount(n: int):
    for i in range(n):
        yield i
