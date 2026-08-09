"""Regression guard for the SearchBuilder reference examples + admin helpers.

Restores the two *real* test classes that were lost when the obsolete
``test_phase2_integration.py`` (which also imported the deleted dead-layer
modules) was removed wholesale. ``examples.py`` is the ``transform_hit``
reference code — it has had real bugs before (unguarded attribute access), so
its per-variant behavior is worth guarding.
"""

from __future__ import annotations


class MockUser:
    id = 1
    pk = 1
    username = "johndoe"
    email = "john@example.com"
    first_name = "John"
    last_name = "Doe"
    is_staff = False
    is_active = True
    is_superuser = False
    date_joined = None
    last_login = None

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"

    class groups:
        @staticmethod
        def count():
            return 3


class TestSearchBuilderExamples:
    """Exercise the SearchBuilder protocol via the reference examples."""

    def test_user_variants_declared(self):
        from apps.search.examples import UserSearchBuilderExample

        variants = UserSearchBuilderExample().get_search_variants()
        assert {"admin", "public", "api"} <= set(variants)

    def test_admin_variant_exposes_email_and_staff(self):
        from apps.search.examples import UserSearchBuilderExample

        hit = UserSearchBuilderExample().transform_hit(MockUser(), variant="admin")
        assert hit["display"] == "John Doe"
        assert hit["email"] == "john@example.com"
        assert "is_staff" in hit

    def test_public_variant_hides_sensitive_fields(self):
        from apps.search.examples import UserSearchBuilderExample

        hit = UserSearchBuilderExample().transform_hit(MockUser(), variant="public")
        assert hit["display"] == "John Doe"
        assert "email" not in hit
        assert "is_staff" not in hit

    def test_api_variant_adds_computed_fields(self):
        from apps.search.examples import UserSearchBuilderExample

        hit = UserSearchBuilderExample().transform_hit(MockUser(), variant="api")
        assert "is_admin" in hit
        assert "groups_count" in hit

    def test_filter_scopes_to_active_users(self):
        from apps.search.examples import UserSearchBuilderExample

        class MockQuerySet:
            def filter(self, **kwargs):
                assert kwargs.get("is_active") is True
                return self

        qs = MockQuerySet()
        assert UserSearchBuilderExample().filter_searchable_queryset(qs) is qs

    def test_ticket_computed_fields(self):
        from apps.search.examples import TicketSearchBuilderExample

        class MockTicket:
            id = 1
            title = "Database error"
            description = "Database connection failed"
            priority = 3
            status = "open"
            customer = "Acme Corp"
            created_at = None
            archived = False

        hit = TicketSearchBuilderExample().transform_hit(MockTicket(), variant="agent")
        assert hit["is_urgent"] is True
        assert "is_open" in hit
        assert "needs_attention" in hit


class TestAdminHelpers:
    """The search admin's display helpers."""

    def test_config_summary_and_badge_are_callable(self):
        from apps.search.admin import (
            format_variant_badge,
            get_search_configuration_summary,
        )
        assert callable(get_search_configuration_summary)
        assert callable(format_variant_badge)

    def test_format_variant_badge_renders_label(self):
        from apps.search.admin import format_variant_badge

        badge = format_variant_badge("admin")
        assert isinstance(badge, str)
        assert "admin" in badge.lower()
