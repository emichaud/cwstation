"""The datasets app is registry-backed and ships no models of its own.

A dataset is a decorated function returning a queryset over some *other* app's
model — there is nothing to persist here. This module exists for Django app
convention only.
"""
