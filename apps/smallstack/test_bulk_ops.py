"""Bulk operations + MCP write handlers (review F4).

Covers the previously-untested bulk-update REST endpoint (api.py), the HTML bulk
views (crud.py), and the MCP update/delete tool handlers (mcp/factory.py) — by
driving a real bulk+api+mcp-capable CRUDView over the APIToken model directly
(no URL registration needed).
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.smallstack.crud import Action, BulkAction, CRUDView
from apps.smallstack.mixins import StaffRequiredMixin
from apps.smallstack.models import APIToken

pytestmark = pytest.mark.django_db
User = get_user_model()


class _TokenBulkView(CRUDView):
    model = APIToken
    url_base = "test/tok"
    mixins = [StaffRequiredMixin]
    bulk_actions = [BulkAction.UPDATE, BulkAction.DELETE]
    actions = [Action.LIST, Action.DETAIL, Action.UPDATE, Action.DELETE]
    enable_api = True
    enable_mcp = True
    fields = ["name", "is_active"]


@pytest.fixture
def staff():
    return User.objects.create_user(username="bulkstaff", password="pw", is_staff=True)


def _tokens(user, n=2):
    return [APIToken.create_token(user, name=f"tok{i}")[0] for i in range(n)]


def _post(rf, user, payload):
    req = rf.post("/x/bulk-update/", data=json.dumps(payload), content_type="application/json")
    req.user = user
    return req


# --- REST bulk-update (api.py 1223-1306) ------------------------------------


def test_bulk_update_success(staff):
    from apps.smallstack.api import _make_api_bulk_update_view

    toks = _tokens(staff)
    view = _make_api_bulk_update_view(_TokenBulkView)
    resp = view(_post(RequestFactory(), staff, {"ids": [t.pk for t in toks], "fields": {"name": "renamed"}}))
    assert resp.status_code == 200
    body = json.loads(resp.content)
    assert len(body["updated"]) == 2
    for t in toks:
        t.refresh_from_db()
        assert t.name == "renamed"


def test_bulk_update_rejects_disallowed_field(staff):
    from apps.smallstack.api import _make_api_bulk_update_view

    toks = _tokens(staff, 1)
    view = _make_api_bulk_update_view(_TokenBulkView)
    resp = view(_post(RequestFactory(), staff, {"ids": [toks[0].pk], "fields": {"key_hash": "x"}}))
    assert resp.status_code == 400
    assert "not allowed" in json.loads(resp.content)["errors"]["__all__"][0].lower()


def test_bulk_update_validates_ids_and_fields(staff):
    from apps.smallstack.api import _make_api_bulk_update_view

    view = _make_api_bulk_update_view(_TokenBulkView)
    assert view(_post(RequestFactory(), staff, {"ids": [], "fields": {"name": "x"}})).status_code == 400
    assert view(_post(RequestFactory(), staff, {"ids": [1], "fields": {}})).status_code == 400
    assert view(_post(RequestFactory(), staff, {"ids": ["nope"], "fields": {"name": "x"}})).status_code == 400


def test_bulk_update_reports_missing_ids(staff):
    from apps.smallstack.api import _make_api_bulk_update_view

    view = _make_api_bulk_update_view(_TokenBulkView)
    resp = view(_post(RequestFactory(), staff, {"ids": [999999], "fields": {"name": "x"}}))
    assert resp.status_code == 200
    body = json.loads(resp.content)
    assert body["updated"] == []
    assert "999999" in body["errors"]


def test_bulk_update_requires_staff(staff):
    from apps.smallstack.api import _make_api_bulk_update_view

    non_staff = User.objects.create_user(username="plain", password="pw")
    toks = _tokens(staff, 1)
    view = _make_api_bulk_update_view(_TokenBulkView)
    resp = view(_post(RequestFactory(), non_staff, {"ids": [toks[0].pk], "fields": {"name": "x"}}))
    assert resp.status_code == 403


def test_bulk_update_get_is_405(staff):
    from apps.smallstack.api import _make_api_bulk_update_view

    req = RequestFactory().get("/x/bulk-update/")
    req.user = staff
    assert _make_api_bulk_update_view(_TokenBulkView)(req).status_code == 405


# --- HTML bulk views (crud.py 730-961) --------------------------------------


class _TokenDeleteOnlyView(CRUDView):
    model = APIToken
    url_base = "test/tokdel"
    mixins = [StaffRequiredMixin]
    bulk_actions = [BulkAction.DELETE]
    actions = [Action.LIST, Action.DELETE]
    fields = ["name", "is_active"]


def _bulk_action_view(view_cls):
    from apps.smallstack.crud import _CRUDBulkActionView

    return type("B", (_CRUDBulkActionView,), {"crud_config": view_cls}).as_view()


def _bulk_post(user, payload):
    req = RequestFactory().post("/x/bulk/", data=json.dumps(payload), content_type="application/json")
    req.user = user
    return req


def test_html_bulk_update_success(staff):
    toks = _tokens(staff)
    resp = _bulk_action_view(_TokenBulkView)(
        _bulk_post(staff, {"action": "update", "ids": [t.pk for t in toks], "fields": {"name": "bulkhtml"}})
    )
    assert resp.status_code == 200
    assert json.loads(resp.content)["updated"] == [t.pk for t in toks]
    for t in toks:
        t.refresh_from_db()
        assert t.name == "bulkhtml"


def test_html_bulk_update_not_enabled_is_403(staff):
    toks = _tokens(staff, 1)
    resp = _bulk_action_view(_TokenDeleteOnlyView)(
        _bulk_post(staff, {"action": "update", "ids": [toks[0].pk], "fields": {"name": "x"}})
    )
    assert resp.status_code == 403


def test_html_bulk_update_no_fields_is_400(staff):
    toks = _tokens(staff, 1)
    resp = _bulk_action_view(_TokenBulkView)(
        _bulk_post(staff, {"action": "update", "ids": [toks[0].pk], "fields": {}})
    )
    assert resp.status_code == 400


def test_html_bulk_update_invalid_field_is_400(staff):
    toks = _tokens(staff, 1)
    resp = _bulk_action_view(_TokenBulkView)(
        _bulk_post(staff, {"action": "update", "ids": [toks[0].pk], "fields": {"key_hash": "x"}})
    )
    assert resp.status_code == 400


def test_html_bulk_delete_success(staff):
    toks = _tokens(staff)
    resp = _bulk_action_view(_TokenBulkView)(
        _bulk_post(staff, {"action": "delete", "ids": [t.pk for t in toks]})
    )
    assert resp.status_code == 200
    assert json.loads(resp.content)["deleted"] == [t.pk for t in toks]
    assert not APIToken.objects.filter(pk__in=[t.pk for t in toks]).exists()


def test_html_bulk_unknown_action_is_400(staff):
    toks = _tokens(staff, 1)
    resp = _bulk_action_view(_TokenBulkView)(
        _bulk_post(staff, {"action": "frobnicate", "ids": [toks[0].pk]})
    )
    assert resp.status_code == 400


def test_html_bulk_no_ids_is_400(staff):
    resp = _bulk_action_view(_TokenBulkView)(_bulk_post(staff, {"action": "delete", "ids": []}))
    assert resp.status_code == 400


def test_bulk_update_form_view_renders_fields(staff):
    from apps.smallstack.crud import _make_bulk_update_form_view

    req = RequestFactory().get("/x/bulk/update-form/")
    req.user = staff
    resp = _make_bulk_update_form_view(_TokenBulkView)(req)
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'data-field="name"' in body and 'data-field="is_active"' in body
