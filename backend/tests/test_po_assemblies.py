import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import po_assemblies as pa  # noqa: E402

PO = "po-1"


def line_item(row_id, **fields):
    row = {"$rowID": row_id, pa.LI["poRowId"]: PO, pa.LI["currentStatus"]: "Mfg"}
    row.update({pa.LI[key]: value for key, value in fields.items()})
    return row


class FakeGlide:
    def __init__(self, line_items, projects="Proj A, Proj B"):
        self.tables = {
            pa.LINE_ITEMS_TABLE: line_items,
            pa.PO_TABLE: [{"$rowID": PO, pa.PO_PROJECTS: projects, pa.PO_NUMBER: "PO-77", pa.PO_CUSTOMER: "Acme"}],
            pa.ASSEMBLIES_TABLE: [],
            pa.CHILD_PARTS_TABLE: [],
        }
        self.calls = []
        self.next_id = 0

    async def query(self, table):
        return [dict(r) for r in self.tables[table]]

    async def mutate(self, mutations):
        self.calls.append(mutations)
        results = []
        for m in mutations:
            table = self.tables[m["tableName"]]
            if m["kind"] == "add-row-to-table":
                self.next_id += 1
                row_id = f"new-{self.next_id}"
                table.append({"$rowID": row_id, **m["columnValues"]})
                results.append({"rowID": row_id})
            else:
                row = next(r for r in table if r["$rowID"] == m["rowID"])
                row.update(m["columnValues"])
                results.append({})
        return results


@pytest.fixture(autouse=True)
def reset_state():
    pa._owned.clear()
    pa._locks.clear()


def client_for(glide):
    app = FastAPI()
    app.include_router(pa.router)
    app.dependency_overrides[pa.get_glide] = lambda: glide
    return TestClient(app)


def sample_items():
    return [
        line_item("a", partNumber="P-100", partName="Frame", quantity=2, category="Fabrication"),
        line_item("m", partNumber="WZ_Fasteners_061026", partName="Fasteners", quantity=30,
                  category="Consumables", groupName="Fasteners", groupMaster=True),
        line_item("b", partNumber="B-1", partName="Bolt", quantity=10, partOfGroup=True, groupName="Fasteners"),
        line_item("c", partNumber="N-1", partName="Nut", quantity=20, partOfGroup=True, groupName="Fasteners"),
        line_item("r", partNumber="X", partName="Rejected", rejected=True),
        line_item("d", partNumber="Y", partName="Done", addedAsAssembly=True),
        {"$rowID": "other", pa.LI["poRowId"]: "po-2", pa.LI["partNumber"]: "Z"},
    ]


def test_get_returns_open_rows_groups_and_projects():
    res = client_for(FakeGlide(sample_items())).get(f"/po-assemblies/{PO}")
    assert res.status_code == 200
    body = res.json()
    assert [r["rowId"] for r in body["rows"]] == ["a", "b", "c"]
    assert body["groups"] == [{"name": "Fasteners", "partNumber": "WZ_Fasteners_061026", "rowId": "m", "project": ""}]
    assert body["projects"] == ["Proj A", "Proj B"]
    assert body["po"]["poNumber"] == "PO-77"
    assert body["submittedCount"] == 1


def test_get_unknown_po_is_404():
    assert client_for(FakeGlide(sample_items())).get("/po-assemblies/nope").status_code == 404


def test_save_updates_and_creates():
    glide = FakeGlide(sample_items())
    client = client_for(glide)
    res = client.post(f"/po-assemblies/{PO}/save", json={
        "updates": [{"rowId": "a", "fields": {"quantity": "3", "project": " Proj A "}}],
        "creates": [{"tempId": "t1", "fields": {"partNumber": "NEW", "partName": "New part", "quantity": 1}}],
    })
    assert res.status_code == 200
    new_id = res.json()["created"]["t1"]
    rows = {r["$rowID"]: r for r in glide.tables[pa.LINE_ITEMS_TABLE]}
    assert rows["a"][pa.LI["quantity"]] == 3
    assert rows["a"][pa.LI["project"]] == "Proj A"
    assert rows[new_id][pa.LI["poRowId"]] == PO
    assert rows[new_id][pa.LI["partNumber"]] == "NEW"


def test_save_rejects_other_po_rows_and_unknown_fields():
    client = client_for(FakeGlide(sample_items()))
    assert client.post(f"/po-assemblies/{PO}/save", json={
        "updates": [{"rowId": "other", "fields": {"partName": "x"}}]}).status_code == 403
    assert client.post(f"/po-assemblies/{PO}/save", json={
        "updates": [{"rowId": "a", "fields": {"poRowId": "po-2"}}]}).status_code == 400
    assert client.post(f"/po-assemblies/{PO}/save", json={
        "updates": [{"rowId": "a", "fields": {"quantity": "two"}}]}).status_code == 400


def test_submit_requires_projects():
    res = client_for(FakeGlide(sample_items())).post(f"/po-assemblies/{PO}/submit")
    assert res.status_code == 400
    assert {e["rowId"] for e in res.json()["rowErrors"]} == {"a", "b", "c"}


def test_submit_rejects_group_split_across_projects():
    items = sample_items()
    for row in items[:4]:
        row[pa.LI["project"]] = "Proj A"
    items[3][pa.LI["project"]] = "Proj B"
    res = client_for(FakeGlide(items)).post(f"/po-assemblies/{PO}/submit")
    assert res.status_code == 400
    assert any("same project" in e["message"] for e in res.json()["rowErrors"])


def test_submit_creates_assemblies_and_child_parts():
    items = sample_items()
    for row in items[:4]:
        row[pa.LI["project"]] = "Proj A"
    glide = FakeGlide(items)
    res = client_for(glide).post(f"/po-assemblies/{PO}/submit")
    assert res.status_code == 200, res.text
    assert res.json() == {"standaloneAssemblies": 1, "groupAssemblies": 1, "childParts": 2}

    assemblies = glide.tables[pa.ASSEMBLIES_TABLE]
    assert [(a[pa.ASM["partNumber"]], a[pa.ASM["packageAssembly"]], a[pa.ASM["extractedRowId"]]) for a in assemblies] == [
        ("P-100", False, "a"),
        ("WZ_Fasteners_061026", True, "m"),
    ]
    assert assemblies[0][pa.ASM["project"]] == "Proj A"

    children = glide.tables[pa.CHILD_PARTS_TABLE]
    assert [(c[pa.CP["drawingNumber"]], c[pa.CP["parentDrawingNumber"]], c[pa.CP["quantity"]], c[pa.CP["itemNumber"]])
            for c in children] == [("B-1", "WZ_Fasteners_061026", "10", 1), ("N-1", "WZ_Fasteners_061026", "20", 2)]

    rows = {r["$rowID"]: r for r in glide.tables[pa.LINE_ITEMS_TABLE]}
    for row_id in ("a", "b", "c", "m"):
        assert rows[row_id][pa.LI["addedAsAssembly"]] is True

    # Everything is submitted, so a second submit has nothing to do
    assert client_for(glide).post(f"/po-assemblies/{PO}/submit").status_code == 400


def test_new_group_without_master_uses_its_name():
    items = [
        line_item("x", partNumber="S-1", partName="Screw", quantity=4, partOfGroup=True, groupName="Kit", project="Proj A"),
        line_item("y", partNumber="W-1", partName="Washer", quantity=4, partOfGroup=True, groupName="kit", project="Proj A"),
    ]
    units, errors = pa.build_submit_plan([pa.parse_line_item(r) for r in items], ["Proj A"])
    assert errors == []
    assert len(units) == 1
    assembly = units[0]["mutations"][0]["columnValues"]
    assert assembly[pa.ASM["partNumber"]] == "Kit"
    assert assembly[pa.ASM["partName"]] == "Kit"


def test_batches_never_split_a_unit():
    units = [{"mutations": [None] * 300}, {"mutations": [None] * 300}, {"mutations": [None] * 100}]
    assert [len(b) for b in pa.batch_units(units)] == [1, 2]
