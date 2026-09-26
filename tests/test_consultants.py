"""Booking/availability and reallocation — the exact logic that produced
two real, manually-caught bugs earlier this session (a booking-visibility
gap where a booked slot showed as free, and a consultant-merge migration
bug where bookings landed on the wrong account). Automating what was
previously only checked by hand."""

from app.models import UserRole
from tests.conftest import auth_headers, login, make_user

DATE = "2026-11-02"  # a fixed future date, arbitrary but stable across runs


def test_availability_true_when_a_consultant_is_free(client, db_session):
    make_user(db_session, username="cons_a", role=UserRole.consultant)
    resp = client.get(f"/api/consultants/availability?date={DATE}")
    assert resp.status_code == 200
    slots = {s["time"]: s["available"] for s in resp.json()["slots"]}
    assert slots["09:00"] is True


def test_availability_false_once_every_active_consultant_is_booked(client, db_session):
    make_user(db_session, username="cons_b1", role=UserRole.consultant)
    make_user(db_session, username="cons_b2", role=UserRole.consultant)

    for _ in range(2):
        resp = client.post("/api/consultants/book", json={"date": DATE, "time": "10:00"})
        assert resp.status_code == 201

    resp = client.get(f"/api/consultants/availability?date={DATE}")
    slots = {s["time"]: s["available"] for s in resp.json()["slots"]}
    assert slots["10:00"] is False
    # Untouched slots on the same day stay available — booking one slot
    # must not accidentally mark the whole day busy.
    assert slots["11:00"] is True

    resp = client.post("/api/consultants/book", json={"date": DATE, "time": "10:00"})
    assert resp.status_code == 409


def test_whole_day_block_removes_a_consultant_from_the_pool(client, db_session):
    solo = make_user(db_session, username="cons_c1", role=UserRole.consultant)
    tokens = login(client, "cons_c1")

    resp = client.post(
        f"/api/consultants/{solo.id}/unavailabilities", json={"date": DATE},
        headers=auth_headers(tokens["access_token"]),
    )
    assert resp.status_code == 201

    resp = client.get(f"/api/consultants/availability?date={DATE}")
    slots = {s["time"]: s["available"] for s in resp.json()["slots"]}
    assert slots["09:00"] is False

    resp = client.post("/api/consultants/book", json={"date": DATE, "time": "09:00"})
    assert resp.status_code == 409


def test_reallocation_moves_the_booking_and_rejects_conflicts(client, db_session):
    admin = make_user(db_session, username="cons_d_admin", role=UserRole.admin)
    source = make_user(db_session, username="cons_d_source", role=UserRole.consultant)
    target = make_user(db_session, username="cons_d_target", role=UserRole.consultant)
    admin_tokens = login(client, "cons_d_admin")

    book_date = "2026-11-03"
    # Block target for this slot first so book_slot — which picks whichever
    # active consultant is free — has no choice but to land the booking on
    # source; only these two consultants exist in this test's transaction.
    block_resp = client.post(
        f"/api/consultants/{target.id}/unavailabilities", json={"date": book_date, "time": "14:00"},
        headers=auth_headers(admin_tokens["access_token"]),
    )
    assert block_resp.status_code == 201
    booking_resp = client.post("/api/consultants/book", json={"date": book_date, "time": "14:00"})
    assert booking_resp.status_code == 201

    bookings = client.get(
        f"/api/consultants/{source.id}/bookings?month=2026-11", headers=auth_headers(admin_tokens["access_token"]),
    ).json()
    real_booking = next((b for b in bookings if b["date"] == book_date and b["time"] == "14:00"), None)
    assert real_booking is not None, "expected the 14:00 booking to land on the unblocked consultant (source)"

    # Now unblock target and reallocate the booking onto it.
    unblock_id = client.get(
        f"/api/consultants/{target.id}/unavailabilities?month=2026-11", headers=auth_headers(admin_tokens["access_token"]),
    ).json()[0]["id"]
    client.delete(f"/api/consultants/{target.id}/unavailabilities/{unblock_id}", headers=auth_headers(admin_tokens["access_token"]))

    reallocate = client.patch(
        f"/api/consultants/{source.id}/bookings/{real_booking['id']}/reallocate",
        json={"new_consultant_id": target.id},
        headers=auth_headers(admin_tokens["access_token"]),
    )
    assert reallocate.status_code == 200

    source_bookings = client.get(
        f"/api/consultants/{source.id}/bookings?month=2026-11", headers=auth_headers(admin_tokens["access_token"]),
    ).json()
    assert all(b["id"] != real_booking["id"] for b in source_bookings)

    target_bookings = client.get(
        f"/api/consultants/{target.id}/bookings?month=2026-11", headers=auth_headers(admin_tokens["access_token"]),
    ).json()
    assert any(b["id"] == real_booking["id"] for b in target_bookings)

    # source's slot is free again post-reallocation — book it again, then try
    # to reallocate THAT booking onto target too: target is now already
    # booked at that exact date/time, so it must be rejected as a conflict.
    second_booking = client.post("/api/consultants/book", json={"date": book_date, "time": "14:00"}).json()
    conflict = client.patch(
        f"/api/consultants/{source.id}/bookings/{second_booking['id']}/reallocate",
        json={"new_consultant_id": target.id},
        headers=auth_headers(admin_tokens["access_token"]),
    )
    assert conflict.status_code == 409


def test_reallocate_requires_consultants_edit_permission(client, db_session):
    source = make_user(db_session, username="cons_e_source", role=UserRole.consultant)
    target = make_user(db_session, username="cons_e_target", role=UserRole.consultant)
    tokens = login(client, "cons_e_source")

    booking = client.post("/api/consultants/book", json={"date": "2026-11-05", "time": "09:00"}).json()

    resp = client.patch(
        f"/api/consultants/{source.id}/bookings/{booking['id']}/reallocate",
        json={"new_consultant_id": target.id},
        headers=auth_headers(tokens["access_token"]),
    )
    assert resp.status_code == 403


def test_all_bookings_endpoint_scopes_by_role(client, db_session):
    """Backs the "Rendez-vous" sidebar section — an admin sees every
    consultant's bookings (and can narrow to one via consultant_id); a
    plain consultant only ever sees their own, regardless of what
    consultant_id they pass."""
    make_user(db_session, username="cons_f_admin", role=UserRole.admin)
    consultant_a = make_user(db_session, username="cons_f_a", role=UserRole.consultant)
    consultant_b = make_user(db_session, username="cons_f_b", role=UserRole.consultant)
    admin_tokens = login(client, "cons_f_admin")
    a_tokens = login(client, "cons_f_a")

    # Force each booking onto a specific consultant the same way the
    # reallocation test does: block the other one for that slot first.
    client.post(
        f"/api/consultants/{consultant_b.id}/unavailabilities", json={"date": "2026-11-06", "time": "09:00"},
        headers=auth_headers(admin_tokens["access_token"]),
    )
    booking_a = client.post("/api/consultants/book", json={"date": "2026-11-06", "time": "09:00"}).json()
    client.post(
        f"/api/consultants/{consultant_a.id}/unavailabilities", json={"date": "2026-11-06", "time": "10:00"},
        headers=auth_headers(admin_tokens["access_token"]),
    )
    booking_b = client.post("/api/consultants/book", json={"date": "2026-11-06", "time": "10:00"}).json()

    admin_view = client.get(
        "/api/consultants/bookings?month=2026-11", headers=auth_headers(admin_tokens["access_token"]),
    ).json()
    assert {b["id"] for b in admin_view} >= {booking_a["id"], booking_b["id"]}

    filtered = client.get(
        f"/api/consultants/bookings?month=2026-11&consultant_id={consultant_a.id}",
        headers=auth_headers(admin_tokens["access_token"]),
    ).json()
    assert {b["id"] for b in filtered} == {booking_a["id"]}

    consultant_a_view = client.get(
        "/api/consultants/bookings?month=2026-11", headers=auth_headers(a_tokens["access_token"]),
    ).json()
    assert {b["id"] for b in consultant_a_view} == {booking_a["id"]}
    # A consultant can't use consultant_id to peek at someone else's bookings.
    ignored_filter = client.get(
        f"/api/consultants/bookings?month=2026-11&consultant_id={consultant_b.id}",
        headers=auth_headers(a_tokens["access_token"]),
    ).json()
    assert {b["id"] for b in ignored_filter} == {booking_a["id"]}


def test_cannot_delete_a_user_with_active_bookings_or_blocks(client, db_session):
    """Reproduces a real data-integrity gap found earlier this session:
    consultant_bookings/consultant_unavailabilities have no ON DELETE on
    their consultant_id FK, so deleting a user who still has either used
    to silently orphan those rows (they later rendered as "Consultant
    supprimé" until someone noticed and cleaned the table up by hand).
    Deletion is now blocked instead — reallocate/clear the calendar first."""
    superadmin = make_user(db_session, username="cons_g_super", role=UserRole.superadmin)
    with_booking = make_user(db_session, username="cons_g_booked", role=UserRole.consultant)
    with_block = make_user(db_session, username="cons_g_blocked", role=UserRole.consultant)
    admin_tokens = login(client, "cons_g_super")

    # Force the booking onto with_booking by blocking with_block for that slot.
    client.post(
        f"/api/consultants/{with_block.id}/unavailabilities", json={"date": "2026-11-10", "time": "09:00"},
        headers=auth_headers(admin_tokens["access_token"]),
    )
    booked = client.post("/api/consultants/book", json={"date": "2026-11-10", "time": "09:00"})
    assert booked.status_code == 201

    resp = client.delete(f"/api/users/{with_booking.id}", headers=auth_headers(admin_tokens["access_token"]))
    assert resp.status_code == 409

    resp = client.delete(f"/api/users/{with_block.id}", headers=auth_headers(admin_tokens["access_token"]))
    assert resp.status_code == 409

    # Clearing the block (the only thing standing in the way for this one)
    # lets the deletion through.
    unavailability_id = client.get(
        f"/api/consultants/{with_block.id}/unavailabilities?month=2026-11",
        headers=auth_headers(admin_tokens["access_token"]),
    ).json()[0]["id"]
    client.delete(
        f"/api/consultants/{with_block.id}/unavailabilities/{unavailability_id}",
        headers=auth_headers(admin_tokens["access_token"]),
    )
    resp = client.delete(f"/api/users/{with_block.id}", headers=auth_headers(admin_tokens["access_token"]))
    assert resp.status_code == 204
