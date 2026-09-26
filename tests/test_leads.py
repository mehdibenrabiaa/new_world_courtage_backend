"""Duplicate detection and the activity timeline — both new this session,
neither previously covered by any manual test."""

from app.models import UserRole
from tests.conftest import auth_headers, login, make_user


def test_duplicate_detection_flags_but_does_not_block(client, db_session):
    first = client.post(
        "/api/leads/", json={"type": "Assurance Garage", "name": "Original Lead", "phone": "0655001122"},
    ).json()
    assert first["duplicate_of"] is None

    second = client.post(
        "/api/leads/", json={"type": "Assurance Garage", "name": "Resubmission", "phone": "0655001122"},
    ).json()
    assert second["duplicate_of"] == {"id": first["id"], "name": "Original Lead"}


def test_duplicate_detection_matches_on_email_too(client, db_session):
    first = client.post(
        "/api/leads/",
        json={"type": "Assurance Garage", "name": "Email Match A", "phone": "0611111111", "email": "shared@test.invalid"},
    ).json()
    second = client.post(
        "/api/leads/",
        json={"type": "Assurance Garage", "name": "Email Match B", "phone": "0622222222", "email": "shared@test.invalid"},
    ).json()
    assert second["duplicate_of"]["id"] == first["id"]


def test_activity_log_records_creation_and_field_changes(client, db_session):
    admin = make_user(db_session, username="leads_admin1", role=UserRole.admin)
    tokens = login(client, "leads_admin1")

    lead = client.post(
        "/api/leads/", json={"type": "Assurance Garage", "name": "Timeline Test", "phone": "0699998888"},
    ).json()

    client.patch(
        f"/api/leads/{lead['id']}", json={"status": "contacted", "deal_value": 500},
        headers=auth_headers(tokens["access_token"]),
    )

    activity = client.get(f"/api/leads/{lead['id']}/activity", headers=auth_headers(tokens["access_token"])).json()
    actions = [a["action"] for a in activity]
    assert "created" in actions
    assert "field_changed" in actions

    status_change = next(a for a in activity if a["action"] == "field_changed" and a["field"] == "status")
    assert status_change["old_value"] == "new"
    assert status_change["new_value"] == "contacted"
    assert status_change["actor_name"] == admin.name


def test_consultant_cannot_see_activity_even_on_their_own_lead(client, db_session):
    """The activity timeline shows who did what (reassignments, other
    staff's edits) — a consultant is blocked from it even on a lead
    they're actually allowed to view/edit, unlike every other "leads"
    endpoint which follows the ordinary view/edit permission."""
    admin = make_user(db_session, username="leads_admin4", role=UserRole.admin)
    consultant = make_user(db_session, username="leads_consultant1", role=UserRole.consultant)
    admin_tokens = login(client, "leads_admin4")
    consultant_tokens = login(client, "leads_consultant1")

    lead = client.post(
        "/api/leads/", json={"type": "Assurance Garage", "name": "Hidden Activity Test", "phone": "0655551234"},
    ).json()
    client.patch(
        f"/api/leads/{lead['id']}", json={"assigned_to_id": consultant.id},
        headers=auth_headers(admin_tokens["access_token"]),
    )

    # The consultant can see and edit the lead itself...
    get_resp = client.get(f"/api/leads/{lead['id']}", headers=auth_headers(consultant_tokens["access_token"]))
    assert get_resp.status_code == 200

    # ...but not its activity timeline.
    activity_resp = client.get(
        f"/api/leads/{lead['id']}/activity", headers=auth_headers(consultant_tokens["access_token"]),
    )
    assert activity_resp.status_code == 403

    # An admin still can.
    admin_activity_resp = client.get(
        f"/api/leads/{lead['id']}/activity", headers=auth_headers(admin_tokens["access_token"]),
    )
    assert admin_activity_resp.status_code == 200


def test_activity_log_records_note_and_task_events(client, db_session):
    make_user(db_session, username="leads_admin2", role=UserRole.admin)
    tokens = login(client, "leads_admin2")

    lead = client.post(
        "/api/leads/", json={"type": "Assurance Garage", "name": "Note Task Test", "phone": "0677776666"},
    ).json()

    client.post(
        f"/api/leads/{lead['id']}/notes", json={"content": "Called, no answer"},
        headers=auth_headers(tokens["access_token"]),
    )
    task = client.post(
        f"/api/leads/{lead['id']}/tasks",
        json={"comment": "Follow up", "action": "Call back", "due_date": "2026-12-01"},
        headers=auth_headers(tokens["access_token"]),
    ).json()
    client.patch(
        f"/api/leads/tasks/{task['id']}", json={"completed": True},
        headers=auth_headers(tokens["access_token"]),
    )

    activity = client.get(f"/api/leads/{lead['id']}/activity", headers=auth_headers(tokens["access_token"])).json()
    actions = [a["action"] for a in activity]
    assert "note_added" in actions
    assert "task_added" in actions
    assert "task_completed" in actions


def test_cross_lead_tasks_endpoint_scopes_by_role(client, db_session):
    """A consultant only ever sees tasks on their own leads; an admin sees
    every consultant's tasks, and can narrow to one via assigned_to_id —
    backs the "Tâches" sidebar section."""
    make_user(db_session, username="tasks_admin", role=UserRole.admin)
    consultant_a = make_user(db_session, username="tasks_consultant_a", role=UserRole.consultant)
    consultant_b = make_user(db_session, username="tasks_consultant_b", role=UserRole.consultant)
    admin_tokens = login(client, "tasks_admin")
    a_tokens = login(client, "tasks_consultant_a")

    lead_a = client.post(
        "/api/leads/", json={"type": "Assurance Garage", "name": "Lead A", "phone": "0688880001"},
    ).json()
    client.patch(
        f"/api/leads/{lead_a['id']}", json={"assigned_to_id": consultant_a.id},
        headers=auth_headers(admin_tokens["access_token"]),
    )
    lead_b = client.post(
        "/api/leads/", json={"type": "Assurance Garage", "name": "Lead B", "phone": "0688880002"},
    ).json()
    client.patch(
        f"/api/leads/{lead_b['id']}", json={"assigned_to_id": consultant_b.id},
        headers=auth_headers(admin_tokens["access_token"]),
    )

    client.post(
        f"/api/leads/{lead_a['id']}/tasks",
        json={"comment": "c", "action": "Call A", "due_date": "2026-12-01"},
        headers=auth_headers(admin_tokens["access_token"]),
    )
    client.post(
        f"/api/leads/{lead_b['id']}/tasks",
        json={"comment": "c", "action": "Call B", "due_date": "2026-12-02"},
        headers=auth_headers(admin_tokens["access_token"]),
    )

    admin_view = client.get("/api/leads/tasks", headers=auth_headers(admin_tokens["access_token"])).json()
    assert {t["action"] for t in admin_view} == {"Call A", "Call B"}

    consultant_a_view = client.get("/api/leads/tasks", headers=auth_headers(a_tokens["access_token"])).json()
    assert {t["action"] for t in consultant_a_view} == {"Call A"}

    filtered = client.get(
        f"/api/leads/tasks?assigned_to_id={consultant_b.id}", headers=auth_headers(admin_tokens["access_token"]),
    ).json()
    assert {t["action"] for t in filtered} == {"Call B"}


def test_stats_endpoint_computes_pipeline_and_conversion(client, db_session):
    make_user(db_session, username="leads_admin3", role=UserRole.admin)
    tokens = login(client, "leads_admin3")

    lead1 = client.post(
        "/api/leads/", json={"type": "Assurance Garage", "name": "Stats A", "phone": "0611110001"},
    ).json()
    lead2 = client.post(
        "/api/leads/", json={"type": "Assurance Garage", "name": "Stats B", "phone": "0611110002"},
    ).json()

    client.patch(
        f"/api/leads/{lead1['id']}", json={"status": "converted", "deal_value": 1000},
        headers=auth_headers(tokens["access_token"]),
    )
    client.patch(
        f"/api/leads/{lead2['id']}", json={"deal_value": 300},
        headers=auth_headers(tokens["access_token"]),
    )

    stats = client.get("/api/leads/stats", headers=auth_headers(tokens["access_token"])).json()
    assert stats["total_leads"] == 2
    assert stats["by_status"]["converted"] == 1
    assert stats["conversion_rate"] == 50.0
    assert stats["converted_value"] == 1000.0
    assert stats["total_pipeline_value"] == 300.0  # lead2 is still "new", lead1 is converted (excluded from pipeline)
