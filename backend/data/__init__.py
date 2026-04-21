"""Data provider abstraction. Swap vendors without touching business logic."""
from .provider import DataProvider, get_provider

__all__ = ["DataProvider", "get_provider"]
