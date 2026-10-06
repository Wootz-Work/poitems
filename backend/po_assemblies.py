"""
PO Assemblies page API.

The page (frontend/po-assemblies.html) shows the line items the PO extractor wrote to the
Glide "Extracted PO Items" table for one PO, lets the user edit them and pick a parent
assembly (a group) and a project for each, and then creates:

- one Assemblies row per standalone item, and
- one Assemblies row per group, with each member added to Child Parts under it.

Every edit is saved straight back to the Extracted PO Items table, so that table is the
working copy and nothing is lost when the page is closed before submitting.
"""
import asyncio
import logging
import os
import time

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

GLIDE_API = "https://api.glideapp.io/api/function"
# Glide accepts at most 500 mutations per mutateTables call
MAX_MUTATIONS_PER_CALL = 500

LINE_ITEMS_TABLE = "native-table-d92757f1-325f-4ec8-87a0-98569c3e215a"
PO_TABLE = "native-table-992ebb81-8eed-4e60-b723-aa4e0efa6af5"
ASSEMBLIES_TABLE = "native-table-0GrR50EycwTGYCIFfPMT"
CHILD_PARTS_TABLE = "native-table-3HZdeQgfDL37ac2rc3kF"

# Extracted PO Items table: code key -> Glide column id
LI = {
    "poRowId": "IhlTs",
    "partNumber": "Name",
    "partName": "NgFQP",
    "quantity": "xXZoM",
    "currentStatus": "W7Gm5",
    "category": "1frCA",
    "partOfGroup": "BMGEr",
    "groupName": "W9ymr",
    "groupMaster": "5e7R4",
    "groupMasterPartNumber": "aZmWe",
    "rejected": "9WSDf",
    "addedAsAssembly": "S6LdV",
    "project": "xHvaN",
    "drawing": "d1hZx",
    "drawings": "yfDL9",
    "accepted": "kz6m3",
    "deferred": "nVpVa",
}

# Purchase Order table
PO_PROJECTS = "4L30B"  # "Added To Projects", comma separated
PO_NUMBER = "VGJKq"
PO_CUSTOMER = "6VtPa"

# Assemblies table
ASM = {
    "project": "5DWpY",
    "partNumber": "Name",
    "partName": "Mzfxa",
    "category": "jdTVs",
    "drawing": "yfaWu",
    "currentStatus": "Jgyps",
    "drawings": "4oKzo",
    "packageAssembly": "S8XG9",
    "extractedRowId": "u53LD",
}

# Child Parts table
CP = {
    "partNumber": "remote\u001dPart number",
    "parentDrawingNumber": "remote\u001dParent drawing number",
    "drawingNumber": "remote\u001dDrawing number",
    "quantity": "remote\u001dQuantity",
    "project": "remote\u001dProject Name",
    "description": "qkM5k",
    "itemNumber": "remote\u001dItem #",
}

# Same constant the extractor writes for every row
CURRENT_STATUS = "Mfg"

STRING_FIELDS = {"partNumber", "partName", "groupName", "project"}
BOOL_FIELDS = {"partOfGroup", "rejected", "groupMaster"}
# Fields the page may write. `rejected` is how the page removes a row.
EDITABLE_FIELDS = STRING_FIELDS | BOOL_FIELDS | {"quantity"}


class GlideClient:
    def __init__(self, api_key, app_id):
        self.api_key = api_key
        self.app_id = app_id
        self._client = None

    def _http(self):
        # One long-lived client keeps the TLS connection to Glide warm between requests
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
        return self._client

    async def query(self, table):
        """All rows of a table, following Glide's pagination."""
        rows, start_at = [], None
        while True:
            query = {"tableName": table, "utc": True}
            if start_at:
                query["startAt"] = start_at
            res = await self._http().post(
                f"{GLIDE_API}/queryTables", json={"appID": self.app_id, "queries": [query]}
            )
            res.raise_for_status()
            result = res.json()[0]
            rows.extend(result.get("rows", []))
            start_at = result.get("next")
            if not start_at:
                return rows

    async def mutate(self, mutations):
        """Runs the mutations in order, at most MAX_MUTATIONS_PER_CALL per call. Returns one result per mutation."""
        results = []
        for start in range(0, len(mutations), MAX_MUTATIONS_PER_CALL):
            batch = mutations[start:start + MAX_MUTATIONS_PER_CALL]
            res = await self._http().post(
                f"{GLIDE_API}/mutateTables", json={"appID": self.app_id, "mutations": batch}
            )
            res.raise_for_status()
            results.extend(res.json())
        return results


_glide = None


def get_glide():
    global _glide
    if _glide is None:
        _glide = GlideClient(os.getenv("GLIDE_API_KEY"), os.getenv("GLIDE_APP_ID"))
    return _glide


# --- Pure helpers ---------------------------------------------------------------

def _text(value):
    return "" if value is None else str(value).strip()


def group_key(name):
    return _text(name).lower()


def parse_line_item(row):
    def flag(key):
        return bool(row.get(LI[key]))

    quantity = row.get(LI["quantity"])
    return {
        "rowId": row.get("$rowID"),
        "partNumber": _text(row.get(LI["partNumber"])),
        "partName": _text(row.get(LI["partName"])),
        "quantity": quantity if isinstance(quantity, (int, float)) and not isinstance(quantity, bool) else None,
        "category": _text(row.get(LI["category"])),
        "currentStatus": _text(row.get(LI["currentStatus"])),
        "partOfGroup": flag("partOfGroup"),
        "groupName": _text(row.get(LI["groupName"])),
        "groupMaster": flag("groupMaster"),
        "project": _text(row.get(LI["project"])),
        "drawing": row.get(LI["drawing"]),
        "drawings": row.get(LI["drawings"]),
        "rejected": flag("rejected"),
        "deferred": flag("deferred"),
        "addedAsAssembly": flag("addedAsAssembly"),
    }


def is_open(item):
    """Not rejected, deferred or already submitted."""
    return not (item["rejected"] or item["deferred"] or item["addedAsAssembly"])


def parse_projects(value):
    projects = []
    for name in _text(value).split(","):
        name = name.strip()
        if name and name not in projects:
            projects.append(name)
    return projects


def coerce_fields(fields):
    """Validates and normalises page-supplied fields. Raises ValueError on anything unexpected."""
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    out = {}
    for key, value in fields.items():
        if key not in EDITABLE_FIELDS:
            raise ValueError(f"field {key!r} cannot be edited")
        if key in STRING_FIELDS:
            out[key] = _text(value)
        elif key in BOOL_FIELDS:
            out[key] = bool(value)
        else:  # quantity
            if value is None or _text(value) == "":
                out[key] = None
            else:
                try:
                    out[key] = float(value)
                except (TypeError, ValueError):
                    raise ValueError(f"quantity {value!r} is not a number")
                if out[key].is_integer():
                    out[key] = int(out[key])
    return out


def to_columns(fields):
    return {LI[key]: value for key, value in fields.items()}


def format_quantity(quantity):
    if isinstance(quantity, float) and quantity.is_integer():
        quantity = int(quantity)
    return str(quantity)


def is_blank(item):
    return not item["partNumber"] and not item["partName"] and item["quantity"] is None


def build_submit_plan(items, projects):
    """
    Turns the PO's line items into Glide work.

    Returns (units, errors). Each unit is one standalone item or one group and carries every
    mutation it needs, including marking its line items as submitted, so a unit is either
    fully written or not written at all as long as it fits in one Glide call.
    `errors` is a list of {rowId, message}; the plan must not run when it is non-empty.
    """
    open_items = [i for i in items if is_open(i)]
    masters = {}
    for item in open_items:
        if item["groupMaster"] and item["groupName"]:
            masters.setdefault(group_key(item["groupName"]), item)

    errors = []
    standalone, groups = [], {}
    for item in open_items:
        if item["groupMaster"] or is_blank(item):
            continue
        if item["partOfGroup"] and item["groupName"]:
            groups.setdefault(group_key(item["groupName"]), []).append(item)
        else:
            standalone.append(item)

    def check_project(item):
        if not item["project"]:
            errors.append({"rowId": item["rowId"], "message": "Select the project to add this item to"})
        elif projects and item["project"] not in projects:
            errors.append({"rowId": item["rowId"], "message": f"Project \"{item['project']}\" is not on this PO"})

    for item in standalone:
        if not item["partNumber"]:
            errors.append({"rowId": item["rowId"], "message": "Assembly number is required"})
        check_project(item)

    for key, members in groups.items():
        for item in members:
            if not item["partNumber"]:
                errors.append({"rowId": item["rowId"], "message": "Assembly number is required"})
            if item["quantity"] is None or item["quantity"] <= 0:
                errors.append({"rowId": item["rowId"], "message": "Quantity is required for an item in a group"})
            check_project(item)
        member_projects = {m["project"] for m in members if m["project"]}
        if len(member_projects) > 1:
            name = members[0]["groupName"]
            for item in members:
                errors.append({
                    "rowId": item["rowId"],
                    "message": f"All items in \"{name}\" must go to the same project",
                })

    if errors:
        return [], errors

    def mark_submitted(row_id, project):
        return {
            "kind": "set-columns-in-row",
            "tableName": LINE_ITEMS_TABLE,
            "rowID": row_id,
            "columnValues": {LI["addedAsAssembly"]: True, LI["project"]: project},
        }

    def assembly_row(project, part_number, part_name, package, source):
        values = {
            ASM["project"]: project,
            ASM["partNumber"]: part_number,
            ASM["partName"]: part_name,
            ASM["packageAssembly"]: package,
        }
        if source:
            values[ASM["extractedRowId"]] = source["rowId"]
            if source["category"]:
                values[ASM["category"]] = source["category"]
            values[ASM["currentStatus"]] = source["currentStatus"] or CURRENT_STATUS
            if source["drawing"]:
                values[ASM["drawing"]] = source["drawing"]
            if source["drawings"]:
                values[ASM["drawings"]] = source["drawings"]
        else:
            values[ASM["currentStatus"]] = CURRENT_STATUS
        return {"kind": "add-row-to-table", "tableName": ASSEMBLIES_TABLE, "columnValues": values}

    units = []
    for item in standalone:
        units.append({
            "kind": "standalone",
            "label": item["partNumber"],
            "rowIds": [item["rowId"]],
            "mutations": [
                assembly_row(item["project"], item["partNumber"], item["partName"], False, item),
                mark_submitted(item["rowId"], item["project"]),
            ],
        })

    for key, members in groups.items():
        master = masters.get(key)
        name = members[0]["groupName"]
        part_number = (master and master["partNumber"]) or name
        part_name = (master and master["partName"]) or name
        project = members[0]["project"]
        mutations = [assembly_row(project, part_number, part_name, True, master)]
        for index, item in enumerate(members, start=1):
            mutations.append({
                "kind": "add-row-to-table",
                "tableName": CHILD_PARTS_TABLE,
                "columnValues": {
                    CP["partNumber"]: item["partNumber"],
                    CP["parentDrawingNumber"]: part_number,
                    CP["drawingNumber"]: item["partNumber"],
                    CP["quantity"]: format_quantity(item["quantity"]),
                    CP["project"]: project,
                    CP["description"]: item["partName"],
                    CP["itemNumber"]: index,
                },
            })
        row_ids = [m["rowId"] for m in members]
        if master:
            row_ids.append(master["rowId"])
        mutations.extend(mark_submitted(row_id, project) for row_id in row_ids)
        units.append({
            "kind": "group",
            "label": part_number,
            "rowIds": row_ids,
            "childParts": len(members),
            "mutations": mutations,
        })

    return units, []


def batch_units(units):
    """Packs whole units into Glide calls of at most MAX_MUTATIONS_PER_CALL mutations."""
    batches, current, size = [], [], 0
    for unit in units:
        n = len(unit["mutations"])
        if current and size + n > MAX_MUTATIONS_PER_CALL:
            batches.append(current)
            current, size = [], 0
        current.append(unit)
        size += n
    if current:
        batches.append(current)
    return batches


# --- Routes -------------------------------------------------------------------

router = APIRouter(prefix="/po-assemblies")

# Serialises saves and submits per PO, so a submit never runs between two halves of a save
_locks = {}
# Row ids known to belong to each PO, so a save cannot touch another PO's rows
_owned = {}


def _lock(po_row_id):
    return _locks.setdefault(po_row_id, asyncio.Lock())


def _error(status, message, **extra):
    return JSONResponse(status_code=status, content={"error": message, **extra})


async def _load_items(glide, po_row_id):
    rows = await glide.query(LINE_ITEMS_TABLE)
    items = [parse_line_item(r) for r in rows if r.get(LI["poRowId"]) == po_row_id]
    _owned[po_row_id] = {i["rowId"] for i in items}
    return items


async def _load_po(glide, po_row_id):
    rows = await glide.query(PO_TABLE)
    return next((r for r in rows if r.get("$rowID") == po_row_id), None)


async def _owns(glide, po_row_id, row_ids):
    if row_ids <= _owned.get(po_row_id, set()):
        return True
    await _load_items(glide, po_row_id)
    return row_ids <= _owned[po_row_id]


@router.get("/{po_row_id}")
async def get_po_assemblies(po_row_id: str, glide: GlideClient = Depends(get_glide)):
    started = time.monotonic()
    try:
        items, po = await asyncio.gather(_load_items(glide, po_row_id), _load_po(glide, po_row_id))
    except httpx.HTTPError as e:
        logger.exception("po-assemblies load failed")
        return _error(502, f"Could not load from Glide: {e}")
    if po is None:
        return _error(404, "Purchase order not found")

    open_items = [i for i in items if is_open(i)]
    groups = [
        {"name": i["groupName"], "partNumber": i["partNumber"], "rowId": i["rowId"], "project": i["project"]}
        for i in open_items if i["groupMaster"] and i["groupName"]
    ]
    rows = [
        {key: i[key] for key in ("rowId", "partNumber", "partName", "quantity", "partOfGroup", "groupName", "project", "category")}
        for i in open_items if not i["groupMaster"]
    ]
    logger.info("po-assemblies %s: %d rows, %d groups in %.2fs", po_row_id, len(rows), len(groups), time.monotonic() - started)
    return {
        "po": {"rowId": po_row_id, "poNumber": _text(po.get(PO_NUMBER)), "customer": _text(po.get(PO_CUSTOMER))},
        "projects": parse_projects(po.get(PO_PROJECTS)),
        "groups": groups,
        "rows": rows,
        "submittedCount": sum(1 for i in items if i["addedAsAssembly"] and not i["groupMaster"]),
    }


@router.post("/{po_row_id}/save")
async def save_po_assemblies(po_row_id: str, request: Request, glide: GlideClient = Depends(get_glide)):
    """
    Body: {"updates": [{"rowId", "fields"}], "creates": [{"tempId", "fields"}]}
    Returns {"created": {tempId: rowId}}.
    """
    try:
        body = await request.json()
        updates = [(u["rowId"], coerce_fields(u.get("fields", {}))) for u in body.get("updates", [])]
        creates = [(c["tempId"], coerce_fields(c.get("fields", {}))) for c in body.get("creates", [])]
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return _error(400, f"Invalid save request: {e}")

    async with _lock(po_row_id):
        try:
            if updates and not await _owns(glide, po_row_id, {row_id for row_id, _ in updates}):
                return _error(403, "Some rows do not belong to this purchase order")

            mutations = [
                {"kind": "set-columns-in-row", "tableName": LINE_ITEMS_TABLE, "rowID": row_id, "columnValues": to_columns(fields)}
                for row_id, fields in updates if fields
            ]
            for _, fields in creates:
                values = {LI["poRowId"]: po_row_id, LI["currentStatus"]: CURRENT_STATUS}
                values.update(to_columns(fields))
                mutations.append({"kind": "add-row-to-table", "tableName": LINE_ITEMS_TABLE, "columnValues": values})

            results = await glide.mutate(mutations) if mutations else []
        except httpx.HTTPError as e:
            logger.exception("po-assemblies save failed")
            return _error(502, f"Could not save to Glide: {e}")

        created = {}
        for (temp_id, _), result in zip(creates, results[len(mutations) - len(creates):]):
            created[temp_id] = result.get("rowID")
            _owned.setdefault(po_row_id, set()).add(result.get("rowID"))
    return {"created": created}


@router.post("/{po_row_id}/submit")
async def submit_po_assemblies(po_row_id: str, glide: GlideClient = Depends(get_glide)):
    async with _lock(po_row_id):
        try:
            items, po = await asyncio.gather(_load_items(glide, po_row_id), _load_po(glide, po_row_id))
        except httpx.HTTPError as e:
            logger.exception("po-assemblies submit load failed")
            return _error(502, f"Could not load from Glide: {e}")
        if po is None:
            return _error(404, "Purchase order not found")

        units, errors = build_submit_plan(items, parse_projects(po.get(PO_PROJECTS)))
        if errors:
            return _error(400, "Fix the highlighted rows before submitting", rowErrors=errors)
        if not units:
            return _error(400, "Nothing to submit")

        done = []
        for batch in batch_units(units):
            try:
                await glide.mutate([m for unit in batch for m in unit["mutations"]])
            except httpx.HTTPError as e:
                logger.exception("po-assemblies submit failed after %d units", len(done))
                return _error(
                    502,
                    f"Glide stopped the submit after {len(done)} of {len(units)} assemblies: {e}. "
                    "Reload the page: submitted items disappear from the table, the rest can be submitted again.",
                    submitted=len(done),
                )
            done.extend(batch)

    return {
        "standaloneAssemblies": sum(1 for u in done if u["kind"] == "standalone"),
        "groupAssemblies": sum(1 for u in done if u["kind"] == "group"),
        "childParts": sum(u.get("childParts", 0) for u in done),
    }
