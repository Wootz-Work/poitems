import json
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import po_assemblies as pa  # noqa: E402

PO = "po-1"


def line_item(row_id, **fields):
    row = {
        "$rowID": row_id, pa.LI["poRowId"]: PO, pa.LI["currentStatus"]: "Mfg",
        pa.LI["mfgStartDate"]: "2026-10-20", pa.LI["dispatchDate"]: "2026-11-15",
    }
    row.update({pa.LI[key]: value for key, value in fields.items()})
    return row


class FakeGlide:
    def __init__(self, line_items, projects="Proj A, Proj B"):
        self.tables = {
            pa.LINE_ITEMS_TABLE: line_items,
            pa.PO_TABLE: [{
                "$rowID": PO, pa.PO_PROJECTS: projects, pa.PO_NUMBER: "PO-77", pa.PO_CUSTOMER: "Acme",
                pa.PO_ATTACHMENT_IDS: "g1, g2", pa.PO_BODY: "<p>Please see PO</p>",
            }],
            pa.ASSEMBLIES_TABLE: [],
            pa.CHILD_PARTS_TABLE: [],
            pa.DRAWINGS_TABLE: [],
            pa.USERS_TABLE: [
                {"$rowID": "u1", pa.USER_EMAIL: "boss@x.com", pa.USER_ROLE: "Admin", pa.USER_NAME: "Boss"},
                {"$rowID": "u2", pa.USER_EMAIL: "dev@x.com", pa.USER_ROLE: "User", pa.USER_NAME: "Ayush Singh"},
            ],
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
            elif m["kind"] == "delete-row":
                table[:] = [r for r in table if r["$rowID"] != m["rowID"]]
                results.append({})
            else:
                row = next(r for r in table if r["$rowID"] == m["rowID"])
                row.update(m["columnValues"])
                results.append({})
        return results


@pytest.fixture(autouse=True)
def reset_state():
    pa._owned.clear()
    pa._locks.clear()


class FakeUploader:
    def __init__(self, fail=False):
        self.fail = fail
        self.uploads = []

    async def upload_pdf(self, project, part_number, data):
        if self.fail:
            raise RuntimeError("cloudinary down")
        assert data.startswith(b"%PDF")
        self.uploads.append((project, part_number))
        return f"https://files.test/{pa.safe_file_name(project)}/{pa.safe_file_name(part_number)}.pdf"


class FakeMailer:
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.admin_role = "Admin"
        self.sent = []

    async def send(self, to, cc, subject, body):
        self.sent.append((to, cc, subject, body))


def client_for(glide, uploader=None, mailer=None):
    app = FastAPI()
    app.include_router(pa.router)
    app.dependency_overrides[pa.get_glide] = lambda: glide
    app.dependency_overrides[pa.get_uploader] = lambda: uploader or FakeUploader()
    app.dependency_overrides[pa.get_mailer] = lambda: mailer or FakeMailer()
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
    assert body["groups"] == [
        {"name": "Fasteners", "partNumber": "WZ_Fasteners_061026", "rowId": "m", "project": "", "existing": False}
    ]
    assert body["po"]["attachmentIds"] == ["g1", "g2"]
    assert body["po"]["emailBody"] == "<p>Please see PO</p>"
    assert body["existing"] == []
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
    uploader = FakeUploader()
    res = client_for(glide, uploader).post(f"/po-assemblies/{PO}/submit", json={"user": "dev@x.com"})
    assert res.status_code == 200, res.text
    assert res.json() == {
        "standaloneAssemblies": 1, "groupAssemblies": 1, "childParts": 2,
        "quantityUpdates": 0, "detailUpdates": 0, "removals": 0,
        "drawingsCreated": 4, "drawingsUpdated": 0, "emailSent": False, "warnings": [],
    }

    assemblies = glide.tables[pa.ASSEMBLIES_TABLE]
    assert [(a[pa.ASM["partNumber"]], a[pa.ASM["packageAssembly"]], a[pa.ASM["extractedRowId"]]) for a in assemblies] == [
        ("P-100", False, "a"),
        ("Fasteners", True, "m"),
    ]
    assert assemblies[0][pa.ASM["project"]] == "Proj A"
    assert assemblies[0][pa.ASM["quantity"]] == 2 and assemblies[1][pa.ASM["quantity"]] == 1

    children = glide.tables[pa.CHILD_PARTS_TABLE]
    assert [(c[pa.CP["drawingNumber"]], c[pa.CP["parentDrawingNumber"]], c[pa.CP["quantity"]], c[pa.CP["itemNumber"]])
            for c in children] == [("B-1", "Fasteners", "10", 1), ("N-1", "Fasteners", "20", 2)]

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


def submittable_items():
    items = sample_items()
    for row in items[:4]:
        row[pa.LI["project"]] = "Proj A"
    return items


def test_submit_adds_drawings_linked_to_assemblies():
    glide = FakeGlide(submittable_items())
    uploader = FakeUploader()
    assert client_for(glide, uploader).post(f"/po-assemblies/{PO}/submit").status_code == 200

    assemblies = {a[pa.ASM["partNumber"]]: a["$rowID"] for a in glide.tables[pa.ASSEMBLIES_TABLE]}
    drawings = [
        (d[pa.DWG["partNumber"]], d[pa.DWG["partName"]], d[pa.DWG["quantity"]], d[pa.DWG["assemblyRowId"]], d[pa.DWG["drawing"]])
        for d in glide.tables[pa.DRAWINGS_TABLE]
    ]
    group = assemblies["Fasteners"]
    assert drawings == [
        ("P-100", "Frame", 2, assemblies["P-100"], "https://files.test/Proj A/P-100.pdf"),
        ("Fasteners", "Fasteners", 1, group, "https://files.test/Proj A/Fasteners.pdf"),
        # Children carry the group's part number; their files are named after their own
        ("Fasteners", "Bolt", 10, group, "https://files.test/Proj A/B-1.pdf"),
        ("Fasteners", "Nut", 20, group, "https://files.test/Proj A/N-1.pdf"),
    ]
    assert all(d[pa.DWG["project"]] == "Proj A" and d[pa.DWG["currentStatus"]] == "Mfg" for d in glide.tables[pa.DRAWINGS_TABLE])
    assert [d[pa.DWG["type"]] for d in glide.tables[pa.DRAWINGS_TABLE]] == ["Assembly", "Assembly", "Part", "Part"]
    assert len(uploader.uploads) == 4


def test_existing_drawing_is_updated_not_duplicated():
    glide = FakeGlide(submittable_items())
    glide.tables[pa.DRAWINGS_TABLE].append(
        {"$rowID": "dw1", pa.DWG["project"]: "proj a", pa.DWG["partNumber"]: "fasteners", pa.DWG["drawing"]: "https://real.test/B-1.pdf"}
    )
    uploader = FakeUploader()
    res = client_for(glide, uploader).post(f"/po-assemblies/{PO}/submit")
    assert res.json()["drawingsCreated"] == 3
    assert res.json()["drawingsUpdated"] == 1
    existing = glide.tables[pa.DRAWINGS_TABLE][0]
    assert existing[pa.DWG["quantity"]] == 10
    assert existing[pa.DWG["currentStatus"]] == "Mfg"
    assert existing[pa.DWG["drawing"]] == "https://real.test/B-1.pdf"
    assert pa.DWG["type"] not in existing  # an existing drawing keeps its type
    assert ("Proj A", "B-1") not in uploader.uploads


def test_submit_marks_po_accepted():
    glide = FakeGlide(submittable_items())
    client_for(glide).post(f"/po-assemblies/{PO}/submit", json={"user": "dev@x.com"})
    assert all(a[pa.ASM["internalPoc"]] == "dev@x.com" for a in glide.tables[pa.ASSEMBLIES_TABLE])
    po = glide.tables[pa.PO_TABLE][0]
    assert po[pa.PO_ACCEPTED] is True
    assert po[pa.PO_APPROVED_BY] == "dev@x.com"
    assert po[pa.PO_APPROVED_AT].endswith("Z")


def test_upload_failure_writes_nothing():
    glide = FakeGlide(submittable_items())
    res = client_for(glide, FakeUploader(fail=True)).post(f"/po-assemblies/{PO}/submit")
    assert res.status_code == 502
    assert glide.calls == []


def test_email_only_when_enabled_and_goes_to_admins_cc_submitter():
    off = FakeMailer(enabled=False)
    client_for(FakeGlide(submittable_items()), mailer=off).post(f"/po-assemblies/{PO}/submit", json={"user": "dev@x.com"})
    assert off.sent == []

    on = FakeMailer(enabled=True)
    res = client_for(FakeGlide(submittable_items()), mailer=on).post(f"/po-assemblies/{PO}/submit", json={"user": "dev@x.com"})
    assert res.json()["emailSent"] is True
    to, cc, subject, body = on.sent[0]
    assert to == ["boss@x.com"] and cc == ["dev@x.com"]
    assert subject == "Proj A is now in manufacturing"
    assert body == "Hi team,\n\nProj A is now in manufacturing. Submitted by Ayush Singh\n\nHappy manufacturing!\n"


def test_placeholder_pdf_handles_non_latin_text():
    data = pa.placeholder_pdf("Ø12-A/3", "Bracket \u2264 5mm")
    assert data.startswith(b"%PDF")
    assert pa.safe_file_name("Ø12-A/3") == "Ø12-A-3"


def with_existing(glide):
    """Proj A already has standalone E-1 (drawing qty 5) and group KIT with child K-1 (qty 3)."""
    glide.tables[pa.ASSEMBLIES_TABLE] += [
        {"$rowID": "asm-e1", pa.ASM["project"]: "Proj A", pa.ASM["partNumber"]: "E-1", pa.ASM["partName"]: "Existing one",
         pa.ASM["packageAssembly"]: False, pa.ASM["currentStatus"]: "Mfg", pa.ASM["quantity"]: 5},
        {"$rowID": "asm-kit", pa.ASM["project"]: "Proj A", pa.ASM["partNumber"]: "KIT-1", pa.ASM["partName"]: "Kit",
         pa.ASM["packageAssembly"]: True, pa.ASM["currentStatus"]: "Sampling", pa.ASM["mfgStartDate"]: "2026-10-20T00:00:00.000Z"},
        {"$rowID": "asm-old", pa.ASM["project"]: "Proj A", pa.ASM["partNumber"]: "OLD", pa.ASM["currentStatus"]: "Cancelled"},
        {"$rowID": "asm-other", pa.ASM["project"]: "Proj Z", pa.ASM["partNumber"]: "Z-1"},
    ]
    glide.tables[pa.CHILD_PARTS_TABLE] += [
        {"$rowID": "cp-k1", pa.CP["project"]: "Proj A", pa.CP["parentDrawingNumber"]: "KIT-1", pa.CP["drawingNumber"]: "K-1",
         pa.CP["partNumber"]: "K-1", pa.CP["quantity"]: "3", pa.CP["description"]: "Kit part"},
        {"$rowID": "cp-bom", pa.CP["project"]: "Proj A", pa.CP["parentDrawingNumber"]: "SOME-DRAWING", pa.CP["drawingNumber"]: "X"},
    ]
    glide.tables[pa.DRAWINGS_TABLE] += [
        {"$rowID": "dw-e1", pa.DWG["project"]: "Proj A", pa.DWG["partNumber"]: "E-1", pa.DWG["quantity"]: 4},
        {"$rowID": "dw-k1", pa.DWG["project"]: "Proj A", pa.DWG["partNumber"]: "K-1", pa.DWG["quantity"]: 3},
    ]
    return glide


def test_get_lists_existing_assemblies_and_groups():
    body = client_for(with_existing(FakeGlide(sample_items()))).get(f"/po-assemblies/{PO}").json()
    assert body["existing"] == [
        {"key": "a:asm-e1", "kind": "assembly", "project": "Proj A", "partNumber": "E-1", "partName": "Existing one", "quantity": 5,
         "parent": "", "groupKey": None, "currentStatus": "Mfg", "mfgStartDate": "", "dispatchDate": ""},
        {"key": "c:cp-k1", "kind": "child", "project": "Proj A", "partNumber": "K-1", "partName": "Kit part", "quantity": 3,
         "parent": "Kit", "groupKey": "g:asm-kit", "currentStatus": "Sampling", "mfgStartDate": "2026-10-20", "dispatchDate": ""},
    ]
    assert body["existingGroups"] == [
        {"key": "g:asm-kit", "name": "Kit", "project": "Proj A", "currentStatus": "Sampling", "mfgStartDate": "2026-10-20", "dispatchDate": ""},
    ]
    assert {"name": "Kit", "partNumber": "KIT-1", "rowId": "asm-kit", "project": "Proj A", "existing": True} in body["groups"]


def test_po_line_matching_an_existing_assembly_updates_its_quantity():
    items = [line_item("x", partNumber="e-1", partName="Existing one", quantity=8, project="Proj A")]
    glide = with_existing(FakeGlide(items))
    res = client_for(glide).post(f"/po-assemblies/{PO}/submit")
    assert res.status_code == 200, res.text
    assert res.json()["quantityUpdates"] == 1
    assert res.json()["standaloneAssemblies"] == 0
    assert len(glide.tables[pa.ASSEMBLIES_TABLE]) == 4  # nothing created
    drawings = {d["$rowID"]: d for d in glide.tables[pa.DRAWINGS_TABLE]}
    assert drawings["dw-e1"][pa.DWG["quantity"]] == 8
    assert {a["$rowID"]: a for a in glide.tables[pa.ASSEMBLIES_TABLE]}["asm-e1"][pa.ASM["quantity"]] == 8
    assert glide.tables[pa.LINE_ITEMS_TABLE][0][pa.LI["addedAsAssembly"]] is True


def test_group_matching_an_existing_group_adds_children_to_it():
    items = [line_item("x", partNumber="K-2", partName="New kit part", quantity=4, partOfGroup=True, groupName="kit", project="Proj A")]
    glide = with_existing(FakeGlide(items))
    res = client_for(glide).post(f"/po-assemblies/{PO}/submit")
    assert res.status_code == 200, res.text
    assert res.json()["groupAssemblies"] == 0 and res.json()["childParts"] == 1
    child = glide.tables[pa.CHILD_PARTS_TABLE][-1]
    assert child[pa.CP["parentDrawingNumber"]] == "KIT-1"
    assert child[pa.CP["itemNumber"]] == 2  # after the existing child
    drawing = glide.tables[pa.DRAWINGS_TABLE][-1]
    assert drawing[pa.DWG["partNumber"]] == "KIT-1" and drawing[pa.DWG["assemblyRowId"]] == "asm-kit"
    assert drawing[pa.DWG["drawing"]].endswith("/K-2.pdf")


def test_existing_changes_update_quantity_name_and_remove():
    glide = with_existing(FakeGlide([]))
    res = client_for(glide).post(f"/po-assemblies/{PO}/submit", json={"existingChanges": [
        {"key": "a:asm-e1", "quantity": "7", "partName": "Renamed"},
        {"key": "c:cp-k1", "remove": True},
        {"key": "a:unknown", "remove": True},
    ]})
    assert res.status_code == 200, res.text
    assert res.json()["quantityUpdates"] == 1 and res.json()["removals"] == 1
    asm = {a["$rowID"]: a for a in glide.tables[pa.ASSEMBLIES_TABLE]}
    assert asm["asm-e1"][pa.ASM["partName"]] == "Renamed"
    assert asm["asm-e1"][pa.ASM["quantity"]] == 7
    drawings = {d["$rowID"]: d for d in glide.tables[pa.DRAWINGS_TABLE]}
    assert drawings["dw-e1"][pa.DWG["quantity"]] == 7
    assert drawings["dw-k1"][pa.DWG["currentStatus"]] == "Cancelled"
    assert "cp-k1" not in {c["$rowID"] for c in glide.tables[pa.CHILD_PARTS_TABLE]}


def test_existing_change_with_bad_quantity_is_rejected():
    glide = with_existing(FakeGlide([]))
    res = client_for(glide).post(f"/po-assemblies/{PO}/submit", json={"existingChanges": [{"key": "a:asm-e1", "quantity": "-1"}]})
    assert res.status_code == 400
    assert res.json()["rowErrors"][0]["rowId"] == "a:asm-e1"
    assert glide.calls == []


def test_quantity_is_required_for_standalone_items():
    items = [line_item("x", partNumber="S-1", project="Proj A")]
    res = client_for(FakeGlide(items)).post(f"/po-assemblies/{PO}/submit")
    assert res.status_code == 400
    assert res.json()["rowErrors"] == [{"rowId": "x", "message": "Quantity is required"}]


class FakeDownloader:
    def __init__(self):
        self.calls = []

    async def download(self, file_id):
        self.calls.append(file_id)
        if file_id == "private":
            raise ValueError("not public")
        return b"%PDF-1.4 test", "application/pdf"


def attachment_client(glide, downloader):
    app = FastAPI()
    app.include_router(pa.router)
    app.dependency_overrides[pa.get_glide] = lambda: glide
    app.dependency_overrides[pa.get_downloader] = lambda: downloader
    return TestClient(app)


def test_attachment_proxy_serves_only_the_pos_files():
    pa._attachment_ids.clear()
    glide = FakeGlide(sample_items())
    glide.tables[pa.PO_TABLE][0][pa.PO_ATTACHMENT_IDS] = "1AbCdEfGhIjK, private_file_0"
    downloader = FakeDownloader()
    client = attachment_client(glide, downloader)
    res = client.get(f"/po-assemblies/{PO}/attachments/0")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf" and res.content.startswith(b"%PDF")
    assert downloader.calls == ["1AbCdEfGhIjK"]
    assert client.get(f"/po-assemblies/{PO}/attachments/5").status_code == 404
    assert client.get("/po-assemblies/nope/attachments/0").status_code == 404


def test_status_and_dates_are_saved_and_copied_to_assemblies():
    items = [
        line_item("x", partNumber="S-1", partName="Shaft", quantity=2, project="Proj A",
                  currentStatus="Sampling", mfgStartDate="2026-10-10T00:00:00.000Z", dispatchDate="2026-11-01"),
        line_item("y", partNumber="K-1", partName="Kit part", quantity=1, project="Proj A",
                  partOfGroup=True, groupName="Kit", mfgStartDate="2026-10-12"),
    ]
    glide = FakeGlide(items)
    client = client_for(glide)
    body = client.get(f"/po-assemblies/{PO}").json()
    assert body["rows"][0]["mfgStartDate"] == "2026-10-10" and body["statuses"] == ["Mfg", "Sampling"]
    assert client.post(f"/po-assemblies/{PO}/save", json={"updates": [{"rowId": "x", "fields": {"currentStatus": "Bogus"}}]}).status_code == 400
    assert client.post(f"/po-assemblies/{PO}/save", json={"updates": [{"rowId": "x", "fields": {"dispatchDate": "soon"}}]}).status_code == 400
    assert client.post(f"/po-assemblies/{PO}/save", json={"updates": [{"rowId": "x", "fields": {"dispatchDate": "2026-11-02"}}]}).status_code == 200

    assert client.post(f"/po-assemblies/{PO}/submit").status_code == 200
    asm = {a[pa.ASM["partNumber"]]: a for a in glide.tables[pa.ASSEMBLIES_TABLE]}
    assert asm["S-1"][pa.ASM["currentStatus"]] == "Sampling"
    assert asm["S-1"][pa.ASM["mfgStartDate"]] == "2026-10-10"
    assert asm["S-1"][pa.ASM["dispatchDate"]] == "2026-11-02"
    # A group takes the name shown on the page as its number and name, and its members' dates
    assert asm["Kit"][pa.ASM["partName"]] == "Kit"
    assert asm["Kit"][pa.ASM["mfgStartDate"]] == "2026-10-12"
    assert glide.tables[pa.CHILD_PARTS_TABLE][0][pa.CP["parentDrawingNumber"]] == "Kit"
    drawings = {d[pa.DWG["partNumber"]]: d for d in glide.tables[pa.DRAWINGS_TABLE]}
    assert drawings["S-1"][pa.DWG["currentStatus"]] == "Sampling"


def test_sniff_content_type_trusts_the_file_bytes():
    assert pa.sniff_content_type(b"%PDF-1.7 ...", "application/octet-stream") == "application/pdf"
    assert pa.sniff_content_type(b"\x89PNG\r\n\x1a\n....", "application/octet-stream") == "image/png"
    assert pa.sniff_content_type(b"\xff\xd8\xff\xe0..", "binary/octet-stream") == "image/jpeg"
    assert pa.sniff_content_type(b"PK\x03\x04 docx", "application/octet-stream") == "application/octet-stream"


def test_dates_are_required_for_new_assemblies_only():
    items = [
        line_item("x", partNumber="S-1", quantity=2, project="Proj A", mfgStartDate="", dispatchDate=""),
        line_item("y", partNumber="E-1", quantity=8, project="Proj A", mfgStartDate="", dispatchDate=""),
    ]
    res = client_for(with_existing(FakeGlide(items))).post(f"/po-assemblies/{PO}/submit")
    assert res.status_code == 400
    assert res.json()["rowErrors"] == [
        {"rowId": "x", "message": "Mfg start date is required"},
        {"rowId": "x", "message": "Dispatch date is required"},
    ]


def test_drawing_file_number_reads_the_file_name():
    assert pa.drawing_file_number("https://res.cloudinary.com/x/raw/upload/v1/po-drawings/Proj%20A/B-1.pdf") == "B-1"
    assert pa.drawing_file_number("https://x.test/a/593G7HH-0100-0350.PDF?dl=1") == "593G7HH-0100-0350"
    assert pa.drawing_file_number(None) == ""


def test_email_lists_every_project_and_falls_back_to_the_email():
    units = [{"project": "Ekta 6"}, {"project": "Ekta 7"}, {"project": "Ekta 6"}]
    subject, body = pa.submitted_email({}, pa.user_name([], "x@y.com"), units)
    assert subject == "Ekta 6, Ekta 7 are now in manufacturing"
    assert "Ekta 6, Ekta 7 are now in manufacturing. Submitted by x@y.com" in body


def test_graph_mailer_gets_a_token_and_sends(monkeypatch):
    import asyncio
    import httpx

    for key, value in {"MS_TENANT_ID": "t1", "MS_CLIENT_ID": "c1", "MS_CLIENT_SECRET": "s1", "MS_SENDER": "ops@wootz.work"}.items():
        monkeypatch.setenv(key, value)
    seen = []

    def handler(request):
        seen.append(request)
        if "login.microsoftonline.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(202)

    mailer = pa.Mailer(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asyncio.run(mailer.send(["boss@x.com"], ["dev@x.com"], "Ekta 6 is now in manufacturing", "Hi team"))
    asyncio.run(mailer.send(["boss@x.com"], [], "again", "Hi"))

    token_calls = [r for r in seen if "login.microsoftonline.com" in str(r.url)]
    sends = [r for r in seen if "graph.microsoft.com" in str(r.url)]
    assert len(token_calls) == 1  # the token is reused
    assert str(token_calls[0].url) == "https://login.microsoftonline.com/t1/oauth2/v2.0/token"
    assert str(sends[0].url) == "https://graph.microsoft.com/v1.0/users/ops@wootz.work/sendMail"
    assert sends[0].headers["authorization"] == "Bearer tok"
    sent = json.loads(sends[0].content)["message"]
    assert sent["toRecipients"] == [{"emailAddress": {"address": "boss@x.com"}}]
    assert sent["ccRecipients"] == [{"emailAddress": {"address": "dev@x.com"}}]
    assert sent["subject"] == "Ekta 6 is now in manufacturing"


def test_graph_mailer_needs_its_settings(monkeypatch):
    import asyncio

    for key in ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET", "MS_SENDER"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="MS_SENDER"):
        asyncio.run(pa.Mailer().send(["a@b.com"], [], "s", "b"))


def test_email_is_on_unless_turned_off(monkeypatch):
    monkeypatch.delenv("PO_EMAIL_ENABLED", raising=False)
    assert pa.Mailer().enabled is True
    monkeypatch.setenv("PO_EMAIL_ENABLED", "false")
    assert pa.Mailer().enabled is False


def test_existing_status_and_dates_can_be_edited():
    glide = with_existing(FakeGlide([]))
    res = client_for(glide).post(f"/po-assemblies/{PO}/submit", json={"existingChanges": [
        {"key": "a:asm-e1", "currentStatus": "Sampling", "dispatchDate": "2026-12-01"},
        {"key": "g:asm-kit", "currentStatus": "Mfg", "mfgStartDate": "2026-10-25"},
    ]})
    assert res.status_code == 200, res.text
    assert res.json()["detailUpdates"] == 2
    asm = {a["$rowID"]: a for a in glide.tables[pa.ASSEMBLIES_TABLE]}
    assert asm["asm-e1"][pa.ASM["currentStatus"]] == "Sampling"
    assert asm["asm-e1"][pa.ASM["dispatchDate"]] == "2026-12-01"
    assert asm["asm-kit"][pa.ASM["currentStatus"]] == "Mfg"
    assert asm["asm-kit"][pa.ASM["mfgStartDate"]] == "2026-10-25"
    drawings = {d["$rowID"]: d for d in glide.tables[pa.DRAWINGS_TABLE]}
    assert drawings["dw-e1"][pa.DWG["currentStatus"]] == "Sampling"


def test_existing_edits_reject_bad_status_or_date():
    glide = with_existing(FakeGlide([]))
    res = client_for(glide).post(f"/po-assemblies/{PO}/submit", json={"existingChanges": [
        {"key": "a:asm-e1", "currentStatus": "Bogus"}, {"key": "g:asm-kit", "dispatchDate": "soon"},
    ]})
    assert res.status_code == 400
    assert {e["rowId"] for e in res.json()["rowErrors"]} == {"a:asm-e1", "g:asm-kit"}
    assert glide.calls == []


def test_po_line_update_still_applies_status_and_date_edits():
    items = [line_item("x", partNumber="E-1", partName="Existing one", quantity=8, project="Proj A")]
    glide = with_existing(FakeGlide(items))
    res = client_for(glide).post(f"/po-assemblies/{PO}/submit", json={"existingChanges": [
        {"key": "a:asm-e1", "quantity": 99, "currentStatus": "Sampling"},
    ]})
    assert res.status_code == 200, res.text
    asm = {a["$rowID"]: a for a in glide.tables[pa.ASSEMBLIES_TABLE]}
    assert asm["asm-e1"][pa.ASM["quantity"]] == 8  # the PO line's quantity wins
    assert asm["asm-e1"][pa.ASM["currentStatus"]] == "Sampling"
