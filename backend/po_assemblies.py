"""
PO Assemblies page API.

The page (frontend/po-assemblies.html) shows the line items the PO extractor wrote to the
Glide "Extracted PO Items" table for one PO, lets the user edit them and pick a parent
assembly (a group) and a project for each, and then creates:

- one Assemblies row per standalone item, and
- one Assemblies row per group, with each member added to Child Parts under it,
- a Drawings row for every assembly and child, with a placeholder PDF to be replaced later,

and marks the PO as accepted.

Every edit is saved straight back to the Extracted PO Items table, so that table is the
working copy and nothing is lost when the page is closed before submitting.
"""
import asyncio
import io
import logging
import os
import re
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

GLIDE_API = "https://api.glideapp.io/api/function"
# Glide accepts at most 500 mutations per mutateTables call
MAX_MUTATIONS_PER_CALL = 500

LINE_ITEMS_TABLE = "native-table-d92757f1-325f-4ec8-87a0-98569c3e215a"
PO_TABLE = "native-table-992ebb81-8eed-4e60-b723-aa4e0efa6af5"
ASSEMBLIES_TABLE = "native-table-0GrR50EycwTGYCIFfPMT"
CHILD_PARTS_TABLE = "native-table-3HZdeQgfDL37ac2rc3kF"
DRAWINGS_TABLE = "native-table-unGdNRqsjTPlBDZB2629"
USERS_TABLE = "native-table-UDMQGdMm5t2u9QY1DxE5"

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
    "mfgStartDate": "pTQeb",
    "dispatchDate": "ERhTI",
}

# Purchase Order table
PO_PROJECTS = "4L30B"  # "Added To Projects", comma separated
PO_NUMBER = "VGJKq"
PO_CUSTOMER = "6VtPa"
PO_ACCEPTED = "K1Nbh"
PO_APPROVED_BY = "zuGSm"
PO_APPROVED_AT = "Kygc4"
PO_ATTACHMENT_IDS = "XOAZw"  # Google Drive file ids, comma separated
PO_BODY = "Name"  # the PO email's body

# Drawings table
DWG = {
    "partNumber": "nlHAO",
    "project": "VQlMl",
    "partName": "Name",
    "drawing": "9iB5E",
    "quantity": "zbUI2",
    "currentStatus": "Sjgh3",
    "assemblyRowId": "fdWAC",
}

# Users table
USER_EMAIL = "Email"
USER_ROLE = "Role"

# Assemblies table
ASM = {
    "project": "5DWpY",
    "partNumber": "Name",
    "partName": "Mzfxa",
    "quantity": "Jby1Y",
    "category": "jdTVs",
    "drawing": "yfaWu",
    "currentStatus": "Jgyps",
    "drawings": "4oKzo",
    "packageAssembly": "S8XG9",
    "extractedRowId": "u53LD",
    "mfgStartDate": "6JmvW",
    "dispatchDate": "QNggY",
    "internalPoc": "HqYCY",
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
# Status set on an existing assembly (and its drawing) that is removed from a project
CANCELLED_STATUS = "Cancelled"

# Statuses the page offers for an item
STATUSES = ("Mfg", "Sampling")

STRING_FIELDS = {"partNumber", "partName", "groupName", "project"}
BOOL_FIELDS = {"partOfGroup", "rejected", "groupMaster"}
DATE_FIELDS = {"mfgStartDate", "dispatchDate"}
# Fields the page may write. `rejected` is how the page removes a row.
EDITABLE_FIELDS = STRING_FIELDS | BOOL_FIELDS | DATE_FIELDS | {"quantity", "currentStatus"}


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


# --- Placeholder drawings ---------------------------------------------------------

PLACEHOLDER_NOTE = "Placeholder drawing - replace with the original drawing"


def _latin1(text):
    # The PDF core fonts only cover Latin-1; anything else becomes "?"
    return text.encode("latin-1", "replace").decode("latin-1")


def placeholder_pdf(part_number, part_name):
    """Portrait A4 page with the part name, part number and a note, centred."""
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_margins(20, 20, 20)
    pdf.set_y(115)
    pdf.set_font("Helvetica", size=26)
    pdf.multi_cell(0, 12, _latin1(part_name or part_number), align="C")
    pdf.ln(6)
    pdf.set_font("Helvetica", size=16)
    pdf.multi_cell(0, 9, _latin1(f"Part number: {part_number}"), align="C")
    pdf.ln(10)
    # The call to action: orange and bold so it stands out on the page
    pdf.set_font("Helvetica", style="B", size=16)
    pdf.set_text_color(230, 110, 0)
    pdf.multi_cell(0, 8, PLACEHOLDER_NOTE, align="C")
    return bytes(pdf.output())


def safe_file_name(name):
    """Keeps the part number as the file name, minus characters that break a URL path."""
    return re.sub(r'[\\/?#%&<>:"|*]+', "-", name).strip() or "drawing"


class CloudinaryUploader:
    def __init__(self):
        import cloudinary

        cloudinary.config(
            cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
            api_key=os.getenv("CLOUDINARY_API_KEY"),
            api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        )

    async def upload_pdf(self, project, part_number, data):
        import cloudinary.uploader

        result = await asyncio.to_thread(
            cloudinary.uploader.upload,
            io.BytesIO(data),
            resource_type="raw",  # required for PDFs
            # One folder per project; the file itself is named after the part number
            public_id=f"po-drawings/{safe_file_name(project)}/{safe_file_name(part_number)}.pdf",
            overwrite=True,
        )
        return result["secure_url"]


_uploader = None


def get_uploader():
    global _uploader
    if _uploader is None:
        _uploader = CloudinaryUploader()
    return _uploader


# --- Email ---------------------------------------------------------------------------

def _env_flag(name):
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


class Mailer:
    """
    Sends the "assemblies added" email over SMTP. Off unless PO_EMAIL_ENABLED is true.
    Env: SMTP_HOST, SMTP_PORT (587 = STARTTLS, 465 = SSL), SMTP_USER, SMTP_PASSWORD, EMAIL_FROM,
    PO_EMAIL_ADMIN_ROLE (the Users table role that receives it, default "Admin").
    """

    def __init__(self):
        self.enabled = _env_flag("PO_EMAIL_ENABLED")
        self.admin_role = os.getenv("PO_EMAIL_ADMIN_ROLE", "Admin")

    def _send(self, message):
        host = os.getenv("SMTP_HOST")
        port = int(os.getenv("SMTP_PORT", "587"))
        user, password = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
        smtp_class = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with smtp_class(host, port, timeout=30) as smtp:
            if port != 465:
                smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(message)

    async def send(self, to, cc, subject, body):
        message = EmailMessage()
        message["From"] = os.getenv("EMAIL_FROM") or os.getenv("SMTP_USER")
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        message["Subject"] = subject
        message.set_content(body)
        await asyncio.to_thread(self._send, message)


_mailer = None


def get_mailer():
    global _mailer
    if _mailer is None:
        _mailer = Mailer()
    return _mailer


def admin_emails(users, role):
    emails = []
    for user in users:
        email = _text(user.get(USER_EMAIL))
        if email and _text(user.get(USER_ROLE)).lower() == role.lower() and email not in emails:
            emails.append(email)
    return emails


def submitted_email(po, submitted_by, units):
    """Static body for now; the wording will be replaced later."""
    po_number = _text(po.get(PO_NUMBER)) or "(no PO number)"
    projects = sorted({unit["project"] for unit in units})
    subject = f"Assemblies added for PO {po_number}"
    body = (
        "Hi,\n\n"
        f"The line items of PO {po_number} ({_text(po.get(PO_CUSTOMER))}) have been added as assemblies "
        f"to {', '.join(projects)} by {submitted_by or 'a user'}.\n\n"
        "Placeholder drawings were created for every assembly and child part. "
        "Please replace them with the original drawings.\n"
    )
    return subject, body


# --- Pure helpers ---------------------------------------------------------------

def _text(value):
    return "" if value is None else str(value).strip()


def group_key(name):
    return _text(name).lower()


def to_date(value):
    """Glide date or ISO timestamp -> "YYYY-MM-DD" ("" when empty or unreadable)."""
    match = re.match(r"\d{4}-\d{2}-\d{2}", _text(value))
    return match.group(0) if match else ""


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
        "mfgStartDate": to_date(row.get(LI["mfgStartDate"])),
        "dispatchDate": to_date(row.get(LI["dispatchDate"])),
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
        elif key == "currentStatus":
            if value not in STATUSES:
                raise ValueError(f"status must be one of {', '.join(STATUSES)}")
            out[key] = value
        elif key in DATE_FIELDS:
            out[key] = to_date(value) or None
            if _text(value) and not out[key]:
                raise ValueError(f"{key} {value!r} is not a date")
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


def to_number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        try:
            number = float(_text(value))
        except ValueError:
            return None
    return int(number) if number.is_integer() else number


def parse_attachment_ids(value):
    return [part.strip() for part in _text(value).split(",") if part.strip()]


# --- Existing assemblies -------------------------------------------------------------

def part_key(project, part_number):
    return (_text(project).lower(), _text(part_number).lower())


def load_existing(projects, assemblies, children, drawings):
    """
    What is already in the PO's projects: standalone assemblies, and the children of package
    (group) assemblies. Cancelled assemblies are left out.
    Returns {"items": [...], "groups": [...]}; an item's quantity comes from its Assemblies row
    (standalone, falling back to its drawing) or its Child Parts row (children).
    """
    wanted = {p.lower() for p in projects}
    drawing_by_key = {}
    for row in drawings:
        drawing_by_key.setdefault(part_key(row.get(DWG["project"]), row.get(DWG["partNumber"])), row)

    items, groups = [], {}
    for row in assemblies:
        project = _text(row.get(ASM["project"]))
        if project.lower() not in wanted or _text(row.get(ASM["currentStatus"])).lower() == CANCELLED_STATUS.lower():
            continue
        part_number = _text(row.get(ASM["partNumber"]))
        part_name = _text(row.get(ASM["partName"]))
        if row.get(ASM["packageAssembly"]):
            groups[part_key(project, part_number)] = {
                "name": part_name or part_number, "partNumber": part_number, "project": project,
                "rowId": row.get("$rowID"), "childCount": 0,
            }
            continue
        drawing = drawing_by_key.get(part_key(project, part_number)) or {}
        items.append({
            "key": f"a:{row.get('$rowID')}", "kind": "assembly", "rowId": row.get("$rowID"),
            "project": project, "partNumber": part_number, "partName": part_name,
            "quantity": to_number(row.get(ASM["quantity"])) if to_number(row.get(ASM["quantity"])) is not None
            else to_number(drawing.get(DWG["quantity"])),
            "parent": "",
            "drawingRowId": drawing.get("$rowID"),
        })

    for row in children:
        project = _text(row.get(CP["project"]))
        group = groups.get(part_key(project, row.get(CP["parentDrawingNumber"])))
        if not group:
            continue  # not a child of a group assembly in these projects
        group["childCount"] += 1
        part_number = _text(row.get(CP["drawingNumber"])) or _text(row.get(CP["partNumber"]))
        drawing = drawing_by_key.get(part_key(project, part_number)) or {}
        items.append({
            "key": f"c:{row.get('$rowID')}", "kind": "child", "rowId": row.get("$rowID"),
            "project": project, "partNumber": part_number, "partName": _text(row.get(CP["description"])),
            "quantity": to_number(row.get(CP["quantity"])), "parent": group["name"],
            "drawingRowId": drawing.get("$rowID"),
        })
    return {"items": items, "groups": list(groups.values())}


def existing_mutations(item, quantity=None, part_name=None, remove=False):
    """Mutations that change an existing assembly or child: quantity, name, or removal."""
    def set_cols(table, row_id, values):
        return {"kind": "set-columns-in-row", "tableName": table, "rowID": row_id, "columnValues": values}

    is_child = item["kind"] == "child"
    mutations = []
    if remove:
        if is_child:
            mutations.append({"kind": "delete-row", "tableName": CHILD_PARTS_TABLE, "rowID": item["rowId"]})
        else:
            mutations.append(set_cols(ASSEMBLIES_TABLE, item["rowId"], {ASM["currentStatus"]: CANCELLED_STATUS}))
        if item["drawingRowId"]:
            mutations.append(set_cols(DRAWINGS_TABLE, item["drawingRowId"], {DWG["currentStatus"]: CANCELLED_STATUS}))
        return mutations
    if quantity is not None and quantity != item["quantity"]:
        if is_child:
            mutations.append(set_cols(CHILD_PARTS_TABLE, item["rowId"], {CP["quantity"]: format_quantity(quantity)}))
        else:
            mutations.append(set_cols(ASSEMBLIES_TABLE, item["rowId"], {ASM["quantity"]: quantity}))
        if item["drawingRowId"]:
            mutations.append(set_cols(DRAWINGS_TABLE, item["drawingRowId"], {DWG["quantity"]: quantity}))
    if part_name is not None and _text(part_name) and _text(part_name) != item["partName"]:
        name = _text(part_name)
        if is_child:
            mutations.append(set_cols(CHILD_PARTS_TABLE, item["rowId"], {CP["description"]: name}))
        else:
            mutations.append(set_cols(ASSEMBLIES_TABLE, item["rowId"], {ASM["partName"]: name}))
        if item["drawingRowId"]:
            mutations.append(set_cols(DRAWINGS_TABLE, item["drawingRowId"], {DWG["partName"]: name}))
    return mutations


EMPTY_EXISTING = {"items": [], "groups": []}


def build_submit_plan(items, projects, existing=EMPTY_EXISTING, changes=(), poc=""):
    """
    Turns the PO's line items into Glide work.

    Returns (units, errors). Each unit is one standalone item or one group and carries every
    mutation it needs, including marking its line items as submitted, so a unit is either
    fully written or not written at all as long as it fits in one Glide call. Each unit also
    lists the drawings to add for it; those are written after the unit, since they need the
    new assembly's row id.
    `errors` is a list of {rowId, message}; the plan must not run when it is non-empty.

    `existing` (load_existing) changes what a line item becomes: one whose project and assembly
    number already exist updates that assembly's quantity instead of creating it, and a group
    whose name matches an existing group assembly in its project adds its members to that
    assembly. `changes` are the page's edits to existing assemblies:
    [{"key", "quantity"?, "partName"?, "remove"?}]. `poc` (the publisher's email) becomes the
    Internal POC of every assembly created.
    """
    existing_by_part = {part_key(i["project"], i["partNumber"]): i for i in existing["items"]}
    existing_by_key = {i["key"]: i for i in existing["items"]}
    existing_groups = {}
    for g in existing["groups"]:
        existing_groups.setdefault(part_key(g["project"], g["name"]), g)
        existing_groups.setdefault(part_key(g["project"], g["partNumber"]), g)

    open_items = [i for i in items if is_open(i)]
    masters = {}
    for item in open_items:
        if item["groupMaster"] and item["groupName"]:
            masters.setdefault(group_key(item["groupName"]), item)

    errors = []
    standalone, groups, matched = [], {}, []
    for item in open_items:
        if item["groupMaster"] or is_blank(item):
            continue
        if item["project"] and part_key(item["project"], item["partNumber"]) in existing_by_part:
            matched.append(item)
        elif item["partOfGroup"] and item["groupName"]:
            groups.setdefault(group_key(item["groupName"]), []).append(item)
        else:
            standalone.append(item)

    def check_project(item):
        if not item["project"]:
            errors.append({"rowId": item["rowId"], "message": "Select the project to add this item to"})
        elif projects and item["project"] not in projects:
            errors.append({"rowId": item["rowId"], "message": f"Project \"{item['project']}\" is not on this PO"})

    def check_required(item):
        if not item["partNumber"]:
            errors.append({"rowId": item["rowId"], "message": "Assembly number is required"})
        if item["quantity"] is None or item["quantity"] <= 0:
            errors.append({"rowId": item["rowId"], "message": "Quantity is required"})
        check_project(item)

    for item in standalone + matched:
        check_required(item)

    for key, members in groups.items():
        for item in members:
            check_required(item)
        member_projects = {m["project"] for m in members if m["project"]}
        if len(member_projects) > 1:
            name = members[0]["groupName"]
            for item in members:
                errors.append({
                    "rowId": item["rowId"],
                    "message": f"All items in \"{name}\" must go to the same project",
                })

    matched_keys = {existing_by_part[part_key(i["project"], i["partNumber"])]["key"] for i in matched}
    edits = []
    for change in changes:
        item = existing_by_key.get(change.get("key"))
        if not item or item["key"] in matched_keys:
            continue  # gone since the page loaded, or updated by its PO line instead
        quantity = change.get("quantity")
        if quantity is not None:
            quantity = to_number(quantity)
            if quantity is None or quantity <= 0:
                errors.append({"rowId": item["key"], "message": "Quantity must be a number above 0"})
                continue
        edits.append((item, quantity, change.get("partName"), bool(change.get("remove"))))

    if errors:
        return [], errors

    def mark_submitted(row_id, project):
        return {
            "kind": "set-columns-in-row",
            "tableName": LINE_ITEMS_TABLE,
            "rowID": row_id,
            "columnValues": {LI["addedAsAssembly"]: True, LI["project"]: project},
        }

    def assembly_row(project, part_number, part_name, package, source, quantity):
        values = {
            ASM["project"]: project,
            ASM["partNumber"]: part_number,
            ASM["partName"]: part_name,
            ASM["packageAssembly"]: package,
        }
        if quantity is not None:
            values[ASM["quantity"]] = quantity
        if poc:
            values[ASM["internalPoc"]] = poc
        if source:
            values[ASM["extractedRowId"]] = source["rowId"]
            if source["category"]:
                values[ASM["category"]] = source["category"]
            values[ASM["currentStatus"]] = source["currentStatus"] or CURRENT_STATUS
            if source["drawing"]:
                values[ASM["drawing"]] = source["drawing"]
            if source["drawings"]:
                values[ASM["drawings"]] = source["drawings"]
            for key in ("mfgStartDate", "dispatchDate"):
                if source.get(key):
                    values[ASM[key]] = source[key]
        else:
            values[ASM["currentStatus"]] = CURRENT_STATUS
        return {"kind": "add-row-to-table", "tableName": ASSEMBLIES_TABLE, "columnValues": values}

    def drawing(project, part_number, part_name, quantity, source):
        return {
            "project": project,
            "partNumber": part_number,
            "partName": part_name,
            "quantity": quantity,
            "currentStatus": (source and source["currentStatus"]) or CURRENT_STATUS,
        }

    units = []
    for item in matched:
        target = existing_by_part[part_key(item["project"], item["partNumber"])]
        units.append({
            "kind": "update",
            "project": item["project"],
            "label": item["partNumber"],
            "rowIds": [item["rowId"]],
            "quantityUpdate": item["quantity"] != target["quantity"],
            "mutations": existing_mutations(target, quantity=item["quantity"]) + [mark_submitted(item["rowId"], item["project"])],
            "drawings": [],
        })

    for item, quantity, part_name, remove in edits:
        mutations = existing_mutations(item, quantity=quantity, part_name=part_name, remove=remove)
        if mutations:
            units.append({
                "kind": "edit",
                "project": item["project"],
                "label": item["partNumber"],
                "rowIds": [],
                "removal": remove,
                "quantityUpdate": not remove and quantity is not None and quantity != item["quantity"],
                "mutations": mutations,
                "drawings": [],
            })

    for item in standalone:
        units.append({
            "kind": "standalone",
            "project": item["project"],
            "label": item["partNumber"],
            "rowIds": [item["rowId"]],
            "mutations": [
                assembly_row(item["project"], item["partNumber"], item["partName"], False, item, item["quantity"]),
                mark_submitted(item["rowId"], item["project"]),
            ],
            "drawings": [drawing(item["project"], item["partNumber"], item["partName"], item["quantity"], item)],
        })

    for key, members in groups.items():
        master = masters.get(key)
        name = members[0]["groupName"]
        project = members[0]["project"]
        target = existing_groups.get(part_key(project, name))
        if target:
            # The group already exists in this project: its members become more of its children
            part_number, part_name = target["partNumber"], target["name"]
            mutations, first_item = [], target["childCount"] + 1
        else:
            # The group's assembly number and name are both the name shown on the page
            part_number = part_name = name
            source = dict(master or {"rowId": None, "category": "", "drawing": None, "drawings": None})
            source.update({
                "currentStatus": members[0]["currentStatus"] or CURRENT_STATUS,
                "mfgStartDate": next((m["mfgStartDate"] for m in members if m["mfgStartDate"]), ""),
                "dispatchDate": next((m["dispatchDate"] for m in members if m["dispatchDate"]), ""),
            })
            mutations, first_item = [assembly_row(project, part_number, part_name, True, source, 1)], 1
        for index, item in enumerate(members, start=first_item):
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
        child_drawings = [drawing(project, item["partNumber"], item["partName"], item["quantity"], item) for item in members]
        units.append({
            "kind": "existing-group" if target else "group",
            "project": project,
            "label": part_number,
            "rowIds": row_ids,
            "childParts": len(members),
            "mutations": mutations,
            # The group's own drawing (new groups only), then one per child, all linked to the group's assembly row
            "drawings": child_drawings if target else [drawing(project, part_number, part_name, 1, members[0])] + child_drawings,
            "fixedAssemblyRowId": target["rowId"] if target else None,
        })

    return units, []


def drawing_key(project, part_number):
    return (_text(project).lower(), _text(part_number).lower())


def plan_drawings(units, existing_rows):
    """
    Decides, for each unit's drawings, whether to add a row or update the existing one for the
    same project and part number. Sets unit["drawingJobs"] and returns the jobs that need a PDF.
    A part number that appears twice in one submit (same project) gets one drawing.
    """
    existing = {}
    for row in existing_rows:
        existing.setdefault(drawing_key(row.get(DWG["project"]), row.get(DWG["partNumber"])), row.get("$rowID"))
    seen = set()
    new_jobs = []
    for unit in units:
        unit["drawingJobs"] = []
        for spec in unit["drawings"]:
            key = drawing_key(spec["project"], spec["partNumber"])
            if key in seen:
                continue
            seen.add(key)
            job = {"spec": spec, "existingRowId": existing.get(key), "url": None}
            unit["drawingJobs"].append(job)
            if not job["existingRowId"]:
                new_jobs.append(job)
    return new_jobs


def drawing_mutation(job, assembly_row_id):
    spec = job["spec"]
    values = {DWG["currentStatus"]: spec["currentStatus"]}
    if spec["quantity"] is not None:
        values[DWG["quantity"]] = spec["quantity"]
    if job["existingRowId"]:
        return {"kind": "set-columns-in-row", "tableName": DRAWINGS_TABLE, "rowID": job["existingRowId"], "columnValues": values}
    values.update({
        DWG["partNumber"]: spec["partNumber"],
        DWG["project"]: spec["project"],
        DWG["partName"]: spec["partName"],
        DWG["drawing"]: job["url"],
    })
    if assembly_row_id:
        values[DWG["assemblyRowId"]] = assembly_row_id
    return {"kind": "add-row-to-table", "tableName": DRAWINGS_TABLE, "columnValues": values}


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


async def _load_existing(glide, po):
    projects = parse_projects(po.get(PO_PROJECTS))
    if not projects:
        return EMPTY_EXISTING, []
    assemblies, children, drawings = await asyncio.gather(
        glide.query(ASSEMBLIES_TABLE), glide.query(CHILD_PARTS_TABLE), glide.query(DRAWINGS_TABLE)
    )
    return load_existing(projects, assemblies, children, drawings), drawings


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
        if po is None:
            return _error(404, "Purchase order not found")
        existing, _ = await _load_existing(glide, po)
    except httpx.HTTPError as e:
        logger.exception("po-assemblies load failed")
        return _error(502, f"Could not load from Glide: {e}")

    open_items = [i for i in items if is_open(i)]
    groups = [
        {"name": i["groupName"], "partNumber": i["partNumber"], "rowId": i["rowId"], "project": i["project"], "existing": False}
        for i in open_items if i["groupMaster"] and i["groupName"]
    ] + [
        {"name": g["name"], "partNumber": g["partNumber"], "rowId": g["rowId"], "project": g["project"], "existing": True}
        for g in existing["groups"]
    ]
    rows = [
        {key: i[key] for key in (
            "rowId", "partNumber", "partName", "quantity", "partOfGroup", "groupName", "project", "category",
            "currentStatus", "mfgStartDate", "dispatchDate",
        )}
        for i in open_items if not i["groupMaster"]
    ]
    logger.info("po-assemblies %s: %d rows, %d groups in %.2fs", po_row_id, len(rows), len(groups), time.monotonic() - started)
    return {
        "po": {
            "rowId": po_row_id,
            "poNumber": _text(po.get(PO_NUMBER)),
            "customer": _text(po.get(PO_CUSTOMER)),
            "attachmentIds": parse_attachment_ids(po.get(PO_ATTACHMENT_IDS)),
            "emailBody": _text(po.get(PO_BODY)),
        },
        "projects": parse_projects(po.get(PO_PROJECTS)),
        "statuses": list(STATUSES),
        "groups": groups,
        "rows": rows,
        "existing": [
            {key: i[key] for key in ("key", "kind", "project", "partNumber", "partName", "quantity", "parent")}
            for i in existing["items"]
        ],
        "submittedCount": sum(1 for i in items if i["addedAsAssembly"] and not i["groupMaster"]),
    }


# --- Attachments ------------------------------------------------------------------

DRIVE_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
MAX_ATTACHMENT_BYTES = 30 * 1024 * 1024
ATTACHMENT_IDS_TTL_S = 300


class DriveDownloader:
    """Downloads a Google Drive file shared as "anyone with the link"."""

    def __init__(self):
        self._client = None

    async def download(self, file_id):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        async with self._client.stream("GET", url) as res:
            res.raise_for_status()
            content_type = res.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
            if content_type == "text/html":
                # Drive answers with a sign-in or warning page when the file is not public
                raise ValueError("The file is not shared as \"anyone with the link\"")
            data = bytearray()
            async for chunk in res.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_ATTACHMENT_BYTES:
                    raise ValueError("The file is larger than 30 MB")
        return bytes(data), content_type


_downloader = None


def get_downloader():
    global _downloader
    if _downloader is None:
        _downloader = DriveDownloader()
    return _downloader


# PO row id -> (loaded at, attachment ids), so opening each tab doesn't re-read the PO table
_attachment_ids = {}


@router.get("/{po_row_id}/attachments/{index}")
async def get_attachment(
    po_row_id: str, index: int, glide: GlideClient = Depends(get_glide), downloader=Depends(get_downloader)
):
    """The PO's index-th attachment, served from here so the page can render (and zoom, copy from) it itself."""
    cached = _attachment_ids.get(po_row_id)
    if cached and time.monotonic() - cached[0] < ATTACHMENT_IDS_TTL_S:
        ids = cached[1]
    else:
        try:
            po = await _load_po(glide, po_row_id)
        except httpx.HTTPError as e:
            return _error(502, f"Could not load from Glide: {e}")
        if po is None:
            return _error(404, "Purchase order not found")
        ids = parse_attachment_ids(po.get(PO_ATTACHMENT_IDS))
        _attachment_ids[po_row_id] = (time.monotonic(), ids)
    if not 0 <= index < len(ids) or not DRIVE_FILE_ID.match(ids[index]):
        return _error(404, "Attachment not found")
    try:
        data, content_type = await downloader.download(ids[index])
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("attachment %s of %s failed: %s", index, po_row_id, e)
        return _error(502, f"Could not download the attachment: {e}")
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, max-age=600"})


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


# Placeholder PDFs uploaded at the same time
UPLOAD_CONCURRENCY = 4


async def _upload_placeholders(uploader, jobs):
    limit = asyncio.Semaphore(UPLOAD_CONCURRENCY)

    async def upload(job):
        spec = job["spec"]
        async with limit:
            data = await asyncio.to_thread(placeholder_pdf, spec["partNumber"], spec["partName"])
            job["url"] = await uploader.upload_pdf(spec["project"], spec["partNumber"], data)

    await asyncio.gather(*(upload(job) for job in jobs))


@router.post("/{po_row_id}/submit")
async def submit_po_assemblies(
    po_row_id: str,
    request: Request,
    glide: GlideClient = Depends(get_glide),
    uploader=Depends(get_uploader),
    mailer=Depends(get_mailer),
):
    """
    Body (optional): {"user": "<email of the person submitting>",
                      "existingChanges": [{"key", "quantity"?, "partName"?, "remove"?}]}.
    Order: placeholder PDFs are uploaded first, so a storage failure writes nothing to Glide. Then
    each batch of assemblies, child parts and line-item marks; then the drawings (they need the new
    assemblies' row ids) and the PO approval; then the email.
    """
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    submitted_by = _text(body.get("user"))
    changes = body.get("existingChanges") or []
    if not isinstance(changes, list) or not all(isinstance(c, dict) for c in changes):
        return _error(400, "existingChanges must be a list of objects")

    async with _lock(po_row_id):
        try:
            items, po = await asyncio.gather(_load_items(glide, po_row_id), _load_po(glide, po_row_id))
            if po is None:
                return _error(404, "Purchase order not found")
            existing, existing_drawings = await _load_existing(glide, po)
        except httpx.HTTPError as e:
            logger.exception("po-assemblies submit load failed")
            return _error(502, f"Could not load from Glide: {e}")

        units, errors = build_submit_plan(items, parse_projects(po.get(PO_PROJECTS)), existing, changes, poc=submitted_by)
        if errors:
            return _error(400, "Fix the highlighted rows before submitting", rowErrors=errors)
        if not units:
            return _error(400, "Nothing to submit")

        new_jobs = plan_drawings(units, existing_drawings)
        try:
            await _upload_placeholders(uploader, new_jobs)
        except Exception as e:
            logger.exception("po-assemblies placeholder upload failed")
            return _error(502, f"Could not create the placeholder drawings, nothing was submitted: {e}")

        done, failure = [], None
        for batch in batch_units(units):
            try:
                results = await glide.mutate([m for unit in batch for m in unit["mutations"]])
            except httpx.HTTPError as e:
                logger.exception("po-assemblies submit failed after %d units", len(done))
                failure = (
                    f"Glide stopped the submit after {len(done)} of {len(units)} assemblies: {e}. "
                    "Reload the page: submitted items disappear from the table, the rest can be submitted again."
                )
                break
            offset = 0
            for unit in batch:
                # A new group's or standalone's assembly row is its first mutation
                if unit.get("fixedAssemblyRowId"):
                    unit["assemblyRowId"] = unit["fixedAssemblyRowId"]
                elif unit["kind"] in ("standalone", "group") and offset < len(results):
                    unit["assemblyRowId"] = (results[offset] or {}).get("rowID")
                offset += len(unit["mutations"])
            done.extend(batch)

        warnings = []
        follow_up = [drawing_mutation(job, unit.get("assemblyRowId")) for unit in done for job in unit["drawingJobs"]]
        if done and not failure:
            follow_up.append({
                "kind": "set-columns-in-row",
                "tableName": PO_TABLE,
                "rowID": po_row_id,
                "columnValues": {
                    PO_ACCEPTED: True,
                    PO_APPROVED_BY: submitted_by,
                    PO_APPROVED_AT: datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                },
            })
        drawings_ok = True
        if follow_up:
            try:
                await glide.mutate(follow_up)
            except httpx.HTTPError as e:
                logger.exception("po-assemblies drawings / PO approval failed")
                drawings_ok = False
                warnings.append(f"The assemblies were created, but adding their drawings or approving the PO failed: {e}")

        email_sent = False
        if mailer.enabled and done and not failure:
            try:
                users = await glide.query(USERS_TABLE)
                to = admin_emails(users, mailer.admin_role)
                cc = [submitted_by] if submitted_by and submitted_by not in to else []
                if to or cc:
                    subject, text = submitted_email(po, submitted_by, done)
                    await mailer.send(to or cc, cc if to else [], subject, text)
                    email_sent = True
            except Exception as e:
                logger.exception("po-assemblies email failed")
                warnings.append(f"The notification email could not be sent: {e}")

    jobs = [job for unit in done for job in unit["drawingJobs"]] if drawings_ok else []
    summary = {
        "standaloneAssemblies": sum(1 for u in done if u["kind"] == "standalone"),
        "groupAssemblies": sum(1 for u in done if u["kind"] == "group"),
        "childParts": sum(u.get("childParts", 0) for u in done),
        "quantityUpdates": sum(1 for u in done if u.get("quantityUpdate")),
        "removals": sum(1 for u in done if u.get("removal")),
        "drawingsCreated": sum(1 for j in jobs if not j["existingRowId"]),
        "drawingsUpdated": sum(1 for j in jobs if j["existingRowId"]),
        "emailSent": email_sent,
        "warnings": warnings,
    }
    if failure:
        return _error(502, failure, submitted=len(done), **summary)
    return summary
