"""Integration tests: SearchBuilder example implementations + admin helpers.

(The variant-cache / orchestration / native-serializer tests were removed with
the dead `apps.search.{api,orchestration,cache,serializers}` layer — those
modules had no runtime importers; runtime search goes through `get_backend()`
directly.)
"""


class MockUser:
    """Mock User model for testing."""
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

    def test_user_search_builder_variants(self):
        """UserSearchBuilderExample returns correct variants."""
        from apps.search.examples import UserSearchBuilderExample

        variants = UserSearchBuilderExample().get_search_variants()
        assert "admin" in variants
        assert "public" in variants
        assert "api" in variants

    def test_user_search_builder_transform_admin(self):
        """UserSearchBuilderExample transforms for admin variant."""
        from apps.search.examples import UserSearchBuilderExample

        hit = UserSearchBuilderExample().transform_hit(MockUser(), variant="admin")
        assert hit["display"] == "John Doe"
        assert hit["email"] == "john@example.com"
        assert "is_staff" in hit

    def test_user_search_builder_transform_public(self):
        """UserSearchBuilderExample transforms for public variant."""
        from apps.search.examples import UserSearchBuilderExample

        hit = UserSearchBuilderExample().transform_hit(MockUser(), variant="public")
        assert hit["display"] == "John Doe"
        assert "email" not in hit  # Hidden in public
        assert "is_staff" not in hit  # Hidden in public

    def test_user_search_builder_transform_api(self):
        """UserSearchBuilderExample transforms for api variant."""
        from apps.search.examples import UserSearchBuilderExample

        hit = UserSearchBuilderExample().transform_hit(MockUser(), variant="api")
        assert "is_admin" in hit  # Computed field
        assert "groups_count" in hit  # Computed field

    def test_user_search_builder_filtering(self):
        """UserSearchBuilderExample filters inactive users."""
        from apps.search.examples import UserSearchBuilderExample

        class MockQuerySet:
            def filter(self, **kwargs):
                assert kwargs.get("is_active") is True
                return self

        qs = MockQuerySet()
        assert UserSearchBuilderExample().filter_searchable_queryset(qs) is qs

    def test_ticket_search_builder_computed_fields(self):
        """TicketSearchBuilderExample computes fields correctly."""
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
        assert "is_urgent" in hit
        assert "is_open" in hit
        assert "needs_attention" in hit
        assert hit["is_urgent"] is True


class TestAdminIntegration:
    """Test admin integration features."""

    def test_admin_config_summary_import(self):
        """Admin module imports correctly."""
        from apps.search.admin import (
            format_variant_badge,
            get_search_configuration_summary,
        )
        assert callable(get_search_configuration_summary)
        assert callable(format_variant_badge)

    def test_format_variant_badge(self):
        """format_variant_badge generates HTML."""
        from apps.search.admin import format_variant_badge

        badge = format_variant_badge("admin")
        assert isinstance(badge, str)
        assert "admin" in badge.lower()
