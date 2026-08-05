"""Tests for the website app views."""

import pytest


@pytest.mark.django_db
class TestHomeIsMonitor:
    """CW Station: `/` is the CW Monitor (starter marketing pages removed)."""

    def test_home_redirects_to_monitor(self, client):
        response = client.get("/")
        assert response.status_code == 302
        assert response.url == "/cw/"

    def test_removed_starter_pages_404(self, client):
        for path in ("/about/", "/getting-started/", "/starter/", "/components/"):
            assert client.get(path).status_code == 404, path


@pytest.mark.django_db
class TestPublicSearchView:
    """Public-site /search/ (editorial "Find anything" design).

    The page is open to everyone — including anonymous visitors. The
    registry's per-view access gate determines what each visitor can
    find. Help docs are visible to everyone; CRUDViews default to
    staff-only and must opt in to broader access (see
    apps/smallstack/docs/search.md).
    """

    def test_anonymous_can_load_the_page(self, client):
        """Anonymous visitors can load /search/ — the page is public."""
        response = client.get("/search/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Find" in content
        assert "anything" in content

    def test_anonymous_sees_no_staff_or_authenticated_sources(self, client):
        """Anonymous visitors only see ANONYMOUS-level CRUDViews (none by
        default) plus the help docs. No User / APIToken / etc. leakage.

        Assertion is "the specific STAFF/AUTH-tier bundled sources are
        hidden" rather than "no model sources visible at all" — round-4
        audit A1 — so the test survives downstream projects that register
        their own ANON-tier CRUDViews per ``building-a-user-facing-site.md``.
        """
        response = client.get("/search/")
        sources = response.context["indexed_sources"]
        model_labels = {s["model_label"] for s in sources if s["kind"] == "model"}
        # Bundled STAFF-tier opt-ins must not leak to anonymous visitors.
        assert "accounts.User" not in model_labels
        assert "smallstack.APIToken" not in model_labels

    def test_authenticated_empty_query_renders_editorial_layout(self, client, django_user_model):
        """Authenticated GET with no query renders the editorial shell."""
        user = django_user_model.objects.create_user(username="searcher", password="testpass")
        client.force_login(user)
        response = client.get("/search/")
        assert response.status_code == 200
        content = response.content.decode()
        # Editorial design tells — "Find anything" serif moment renders.
        assert "Find" in content
        assert "anything" in content
        # No results section without a query.
        assert response.context["total_hits"] == 0
        assert response.context["grouped"] == []

    def test_non_staff_user_does_not_see_staff_only_sources(self, client, django_user_model):
        """Security: non-staff users see the page, but staff-only models
        (User, APIToken — the default) are hidden from the sources panel.

        Assertion is "the specific bundled STAFF-tier sources are hidden"
        rather than "no model sources visible at all" — round-4 audit A1
        — so the test survives downstream projects that register their own
        AUTH-tier CRUDViews per ``building-a-user-facing-site.md``.
        """
        user = django_user_model.objects.create_user(username="non_staff_user", password="testpass")
        client.force_login(user)
        response = client.get("/search/")
        assert response.status_code == 200
        sources = response.context["indexed_sources"]
        model_labels = {s["model_label"] for s in sources if s["kind"] == "model"}
        # Bundled STAFF-tier opt-ins must not leak to non-staff users.
        assert "accounts.User" not in model_labels
        assert "smallstack.APIToken" not in model_labels

    def test_staff_user_sees_staff_only_sources(self, client, django_user_model):
        """Security: staff users see all registered sources, including the
        default staff-only ones (User, APIToken)."""
        staff = django_user_model.objects.create_user(
            username="staff_searcher", password="testpass", is_staff=True
        )
        client.force_login(staff)
        response = client.get("/search/")
        assert response.status_code == 200
        sources = response.context["indexed_sources"]
        # At least the User CRUDView is registered by default and visible to staff.
        assert any(s["kind"] == "model" for s in sources)

    def test_authenticated_with_query_renders_results_shape(self, client, django_user_model):
        """A query renders the results-with-shape context.

        We assert the view's contract (status + context keys + grouped
        shape), not the search backend's recall — the latter depends on
        FTS index state that is exercised in apps/search/tests/.
        """
        user = django_user_model.objects.create_user(username="searcher2", password="testpass")
        client.force_login(user)
        response = client.get("/search/?q=admin")
        assert response.status_code == 200
        ctx = response.context
        assert ctx["query"] == "admin"
        assert "total_hits" in ctx
        assert "grouped" in ctx
        assert isinstance(ctx["grouped"], list)
        # Each grouped entry (if any) carries the documented shape.
        for group in ctx["grouped"]:
            assert "model_label" in group
            assert "model_verbose" in group
            assert "count" in group
            assert "hits" in group

    def test_authenticated_with_no_match_renders_empty_state(self, client, django_user_model):
        """A query with no matches renders the no-results state without crashing."""
        user = django_user_model.objects.create_user(username="searcher3", password="testpass")
        client.force_login(user)
        response = client.get("/search/?q=zzzzzznotfounditem")
        assert response.status_code == 200
        assert response.context["total_hits"] == 0
        assert response.context["grouped"] == []
        # No-results display copy.
        assert "Nothing matched" in response.content.decode()

    def test_nav_link_is_visible_to_everyone(self, client, django_user_model):
        """The Search link is in the website topbar for every visitor — the
        page is open to anonymous users (who can search help docs) and to
        signed-in users (who additionally see whatever they're permitted)."""
        # Anonymous — link present on the (public) search page's own topbar.
        response = client.get("/search/")
        assert 'href="/search/"' in response.content.decode()

        # Authenticated — link still present.
        user = django_user_model.objects.create_user(username="navtest", password="testpass")
        client.force_login(user)
        response = client.get("/search/")
        assert 'href="/search/"' in response.content.decode()
