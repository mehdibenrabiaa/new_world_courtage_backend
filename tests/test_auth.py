"""Refresh-token lifecycle (rotation, reuse-detection, logout) and the
"consultants" permission matrix — the two areas this session added/reworked
and had already broken once each during manual testing (a datetime
timezone bug on refresh, and a permission-check regression during the
Consultant->User merge)."""

from app.models import PermissionAction, PermissionResource, RolePermission, UserRole
from tests.conftest import auth_headers, login, make_user


def test_refresh_rotates_and_reuse_is_detected(client, db_session):
    make_user(db_session, username="auth_alice", role=UserRole.consultant)
    tokens = login(client, "auth_alice")
    refresh_token = tokens["refresh_token"]
    assert tokens["access_token"]

    first = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert first.status_code == 200
    new_refresh_token = first.json()["refresh_token"]
    assert new_refresh_token != refresh_token

    # Reusing the now-rotated-away token is the standard "this token may
    # have leaked" signal — it must fail...
    reused = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert reused.status_code == 401

    # ...and revoke the whole chain, including the token issued from the
    # legitimate rotation a moment ago.
    chained = client.post("/api/auth/refresh", json={"refresh_token": new_refresh_token})
    assert chained.status_code == 401


def test_logout_revokes_the_refresh_token(client, db_session):
    make_user(db_session, username="auth_bob", role=UserRole.consultant)
    tokens = login(client, "auth_bob")

    resp = client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 204

    resp = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


def test_password_change_revokes_existing_sessions(client, db_session):
    superadmin = make_user(db_session, username="auth_super1", role=UserRole.superadmin)
    target = make_user(db_session, username="auth_carol", role=UserRole.consultant)
    admin_tokens = login(client, "auth_super1")
    target_tokens = login(client, "auth_carol")

    resp = client.patch(
        f"/api/users/{target.id}", json={"password": "newpass456"},
        headers=auth_headers(admin_tokens["access_token"]),
    )
    assert resp.status_code == 200

    resp = client.post("/api/auth/refresh", json={"refresh_token": target_tokens["refresh_token"]})
    assert resp.status_code == 401

    # The access token they were already holding must stop working right
    # away too — it hasn't expired, but the session it belongs to has been
    # killed, and it's a plain, unexpired JWT nothing else checks per
    # request unless get_current_user compares it against sessions_revoked_at.
    resp = client.get("/api/auth/me", headers=auth_headers(target_tokens["access_token"]))
    assert resp.status_code == 401


def test_force_logout_invalidates_the_still_valid_access_token_immediately(client, db_session):
    """Reproduces a real bug report: an admin used "force logout" on a
    consultant, then found the consultant could still see everything after
    a page refresh. Cause: revoke_all_refresh_tokens only killed the
    refresh token — the access token already in the consultant's browser
    was untouched and stayed valid for up to ACCESS_TOKEN_TTL. Fixed via
    User.sessions_revoked_at, checked in get_current_user against the
    token's own "iat"."""
    superadmin = make_user(db_session, username="auth_super2", role=UserRole.superadmin)
    target = make_user(db_session, username="auth_dana", role=UserRole.consultant)
    admin_tokens = login(client, "auth_super2")
    target_tokens = login(client, "auth_dana")

    # Confirm the access token actually works before the force logout —
    # otherwise the assertion after it proves nothing.
    resp = client.get("/api/auth/me", headers=auth_headers(target_tokens["access_token"]))
    assert resp.status_code == 200

    resp = client.post(
        f"/api/users/{target.id}/revoke-sessions", headers=auth_headers(admin_tokens["access_token"]),
    )
    assert resp.status_code == 204

    # Same access token, still well within its 30-minute lifetime — must
    # now be rejected immediately, not just on its next natural expiry.
    resp = client.get("/api/auth/me", headers=auth_headers(target_tokens["access_token"]))
    assert resp.status_code == 401

    resp = client.get(
        "/api/leads/", headers=auth_headers(target_tokens["access_token"]),
    )
    assert resp.status_code == 401


def test_consultant_denied_leads_delete_by_default(client, db_session):
    """main.py seeds consultant=view/create/edit (no delete) for the
    "leads" resource on first boot — verify require_permission actually
    enforces that rather than trusting the seed alone."""
    make_user(db_session, username="auth_dave", role=UserRole.consultant)
    tokens = login(client, "auth_dave")

    create = client.post(
        "/api/leads/", json={"type": "Assurance Garage", "name": "Perm Test", "phone": "0600000001"},
    )
    assert create.status_code == 201
    lead_id = create.json()["id"]

    resp = client.delete(f"/api/leads/{lead_id}", headers=auth_headers(tokens["access_token"]))
    assert resp.status_code == 403


def test_admin_can_delete_leads(client, db_session):
    make_user(db_session, username="auth_admin1", role=UserRole.admin)
    tokens = login(client, "auth_admin1")

    create = client.post(
        "/api/leads/", json={"type": "Assurance Garage", "name": "Perm Test 2", "phone": "0600000002"},
    )
    lead_id = create.json()["id"]

    resp = client.delete(f"/api/leads/{lead_id}", headers=auth_headers(tokens["access_token"]))
    assert resp.status_code == 204


def test_consultant_cannot_manage_other_consultants_calendar(client, db_session):
    """The "consultants" resource defaults to zero permissions for the
    consultant role (see main.py's backfill) — self-service (managing your
    OWN calendar) is a separate, always-allowed path (_can_manage in
    routers/consultants.py), so this specifically checks someone else's."""
    me = make_user(db_session, username="auth_erin", role=UserRole.consultant)
    other = make_user(db_session, username="auth_frank", role=UserRole.consultant)
    tokens = login(client, "auth_erin")

    own_calendar = client.get(
        f"/api/consultants/{me.id}/unavailabilities?month=2026-09", headers=auth_headers(tokens["access_token"]),
    )
    assert own_calendar.status_code == 200

    others_calendar = client.get(
        f"/api/consultants/{other.id}/unavailabilities?month=2026-09", headers=auth_headers(tokens["access_token"]),
    )
    assert others_calendar.status_code == 403


def test_supervisor_role_permission_matches_seeded_defaults(db_session):
    """Sanity check on the seed itself (main.py) rather than the
    enforcement path — supervisor gets view/create/edit but not delete on
    every resource except consultants is edit-capable too per the
    dedicated consultants backfill."""
    rows = db_session.query(RolePermission).filter(
        RolePermission.role == UserRole.supervisor, RolePermission.resource == PermissionResource.leads,
    ).all()
    allowed = {r.action for r in rows if r.allowed}
    assert allowed == {PermissionAction.view, PermissionAction.create, PermissionAction.edit}
