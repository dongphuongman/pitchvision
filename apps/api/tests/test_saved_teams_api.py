"""SCR-40 O03 / SCR-14 E02: saved team names + colours (remembered on confirm, admin edits)."""

import uuid

import httpx

from .conftest import Db
from .test_app import assert_problem
from .test_corrections_api import _analysed, client, tasks  # noqa: F401  (fixtures)
from .test_ingest_api import who

URL = "/api/v1/saved-teams"


async def _confirm(c: httpx.AsyncClient, org: uuid.UUID, vid: uuid.UUID, a: str, ca: str, b: str, cb: str,
                   confirmed: bool = True) -> None:  # fmt: skip
    body = {"teams": [{"cluster": 0, "name": a, "color": ca}, {"cluster": 1, "name": b, "color": cb}],
            "confirmed": confirmed}  # fmt: skip
    r = await c.patch(f"/api/v1/results/{vid}/teams", json=body, headers=who(org))
    assert r.status_code == 200, r.text


async def test_confirm_remembers_and_admins_edit(client: httpx.AsyncClient, migrated: Db) -> None:  # noqa: F811
    org = uuid.uuid4()
    v1, _ = await _analysed(migrated, org)
    v2, _ = await _analysed(migrated, org)
    await _confirm(client, org, v1, "Riverside", "#E53935", "Team B", "#1e88e5")  # default name not saved
    await _confirm(client, org, v2, "riverside", "#112233", "Ajax U17", "#ffffff")
    await _confirm(client, org, v2, "Skipped", "#000000", "Ajax U17", "#ffffff", confirmed=False)
    rows = (await client.get(URL, headers=who(org, "viewer"))).json()
    assert [(t["name"], t["color"], t["uses"]) for t in rows] == [("Riverside", "#112233", 2),
                                                                  ("Ajax U17", "#ffffff", 1)]
    assert (await client.get(URL, headers=who(uuid.uuid4(), "viewer"))).json() == []  # RLS

    ajax = rows[1]["id"]
    assert_problem(await client.patch(f"{URL}/{ajax}", json={"name": "x"}, headers=who(org, "editor")), 403,
                   "FORBIDDEN")  # fmt: skip
    assert_problem(await client.patch(f"{URL}/{ajax}", json={"name": " RIVERSIDE "}, headers=who(org, "admin")), 409,
                   "CONFLICT")  # fmt: skip
    r = await client.patch(f"{URL}/{ajax}", json={"name": "  Ajax   U19 ", "color": "#ABCDEF"},
                           headers=who(org, "admin"))  # fmt: skip
    assert r.status_code == 200 and (r.json()["name"], r.json()["color"]) == ("Ajax U19", "#abcdef")
    assert_problem(await client.patch(f"{URL}/{ajax}", json={"color": "red"}, headers=who(org, "admin")), 422,
                   "VALIDATION_FAILED")  # fmt: skip
    assert (await client.delete(f"{URL}/{ajax}", headers=who(org, "admin"))).status_code == 204
    assert_problem(await client.delete(f"{URL}/{ajax}", headers=who(org, "admin")), 404, "NOT_FOUND")
    assert [t["name"] for t in (await client.get(URL, headers=who(org, "viewer"))).json()] == ["Riverside"]
