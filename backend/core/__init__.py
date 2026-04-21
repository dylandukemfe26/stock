"""Shared infrastructure used across analysis modules.

Currently:
  - events.EventBus: tiny in-process pub/sub
  - cache.TTLCache: time-to-live memoization for provider fetches
"""
