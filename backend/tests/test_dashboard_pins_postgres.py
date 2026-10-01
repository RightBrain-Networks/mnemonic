"""Browser preferences order matching work before pagination, without mutating it."""

import pytest

from .test_hierarchy_presentation_matrix_postgres import create_work, work_collection, work_path

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("sort", ["updated", "created", "priority"])
def test_pins_precede_hierarchy_pages_and_lift_children(api, project, work_payload, sort):
    parent = create_work(api, project, work_payload, title="Parent", priority=90)
    child = create_work(api, project, work_payload, title="Child", parent=parent, priority=1)
    create_work(api, project, work_payload, title="Recent", priority=100)
    pin = child["work_item"]["id"]
    params = {"view": "roots", "detail": "full", "sort": sort, "limit": 1,
              "pinned_work_item_ids": [pin]}
    response = api.get(work_collection(project), params=params)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["summary"]["work_item"]["id"] == pin
    assert response.json()["total"] == 3
    children = api.get(work_path(project, parent) + "/children", params={
        "pinned_work_item_ids": [pin],
    })
    assert children.status_code == 200, children.text
    assert children.json()["items"] == []
    ordinary = api.get(work_collection(project), params={"view": "roots", "detail": "full"})
    assert ordinary.json()["total"] == 2
    hidden = api.get(work_collection(project), params={**params, "status": "done"})
    assert hidden.json()["items"] == []
    assert api.get(work_path(project, child) + "/context").json()["work_item"]["version"] == 1


@pytest.mark.parametrize("direction", ["asc", "desc"])
@pytest.mark.parametrize("sort", ["updated_at", "created_at", "priority", "relevance"])
def test_search_pins_precede_both_directions_before_pagination(
    api, project, work_payload, direction, sort,
):
    works = [create_work(api, project, work_payload, title=f"Needle {index}", priority=index)
             for index in range(3)]
    pin = works[1]["work_item"]["id"]
    payload = {"q": "needle", "facets": ["work_items"], "pinned_work_item_ids": [pin],
               "sort": {"by": sort, "direction": direction}, "limit": 1}
    pages = [api.post(f"/api/v1/projects/{project['id']}/search",
                      json={**payload, "offset": offset}) for offset in range(3)]
    assert all(page.status_code == 200 for page in pages), [page.text for page in pages]
    ids = [page.json()["items"][0]["id"] for page in pages]
    assert ids[0] == pin
    assert set(ids) == {work["work_item"]["id"] for work in works}
    filtered = api.post(f"/api/v1/projects/{project['id']}/search", json={
        **payload, "filters": {"work_items": {"status": "done"}},
    })
    assert filtered.json()["items"] == []
