"""A small example of how I pull product data from Odoo through JSON-RPC.

I use the same .env file and PRODUCT_SKU as odoo_read_and_write.py. This script
finds one product, reads its current data, and shows the technical field format
you use when building your own Odoo requests.

This also shows which fields Odoo marks as writable and displays an example of
how an update would be formatted. The example is never sent to Odoo. This file
does not write, create, or delete anything.

Run:

    python3 odoo_pull_product_data.py
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast

import requests
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# 1. LOAD THE SAME CONFIGURATION
# ---------------------------------------------------------------------------

# First keep API keys and passwords in .env instead of hard-coding them here.
# You should add .env to .gitignore so GitHub never receives those.
load_dotenv()

# ODOO_BASE tells my script where the Odoo server is.
# ODOO_DB tells Odoo which database I want to use on that server.
# rstrip("/") removes a final slash so the endpoint URLs form correctly.
ODOO_BASE = os.getenv("ODOO_BASE", "").strip().rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "").strip()

# I use the same credentials as odoo_read_and_write.py to authenticate.
# OD_PASS may contain your Odoo password or an Odoo API key.
# The script only gets the permissions already assigned to this user.
ODOO_EMAIL = os.getenv("OD_EMAIL", "").strip()
ODOO_PASS = os.getenv("OD_PASS", "").strip()

# VERIFY_SSL checks the server's HTTPS certificate; you normally leave it true.
# ODOO_TIMEOUT is how many seconds you wait for an Odoo response.
VERIFY_SSL = os.getenv("VERIFY_SSL", "true").strip().lower() == "true"
ODOO_TIMEOUT = int(os.getenv("ODOO_TIMEOUT", "30"))

# You choose the product by changing PRODUCT_SKU in the same .env file.
# This handles one SKU per run so the target is always clear.
PRODUCT_SKU = os.getenv("PRODUCT_SKU", "").strip()

# I check two related Odoo models when I look for the product:
# product.product stores the individual variant and its SKU.
# product.template stores the main product and website content fields.
VARIANT_MODEL = "product.product"
TEMPLATE_MODEL = "product.template"

# This is the only list you normally need to change.
# It controls which product fields appear in the terminal.
FIELDS_TO_SHOW: List[str] = [
    "id",
    "name",
    "default_code",
    "meta_title",
    "meta_description",
    "list_price",

    # To show another field, remove the # before it.
    # You can also type another Odoo technical field name into this list.
    # "barcode",
    # "active",
    # "standard_price",
    # "weight",
    # "type",
    # "description_short_overridden_md",
    # "description_long_overridden_md",
    # "categ_id",
    # "uom_id",
    # "qty_available",
    # "mpn",
    # "nsn",
    # "pies_weight",
]

# Leave this False when you only want the fields in FIELDS_TO_SHOW.
# Uncomment True when you want to discover every available Odoo field.
SHOW_ALL_FIELDS = False
# SHOW_ALL_FIELDS = True

# False gives you the short terminal report used in the presentation.
# Uncomment True only when you want the full technical JSON for every selected
# field, including help text, relations, selection options, and update examples.
SHOW_TECHNICAL_DETAILS = False
# SHOW_TECHNICAL_DETAILS = True

# I safely pull these common value types when SHOW_ALL_FIELDS is enabled.
# Large binary images and relationship lists still appear in the schema, but I
# do not download those values in the full-field discovery view.
SAFE_FIELD_TYPES_TO_PULL = {
    "boolean",
    "integer",
    "float",
    "monetary",
    "char",
    "text",
    "html",
    "date",
    "datetime",
    "selection",
    "many2one",
}


# ---------------------------------------------------------------------------
# 2. ODOO JSON-RPC CLIENT
# ---------------------------------------------------------------------------

class OdooJSONRPC:
    """I keep all of my read-only Odoo connection methods in this class."""

    def __init__(self, base_url: str, verify: bool = True, timeout: int = 30):
        # I save the connection settings once and reuse them for every call.
        self.base = base_url.rstrip("/")
        self.verify = verify
        self.timeout = timeout

        # Session stores Odoo's login cookie after authentication.
        # That keeps later requests logged in as the same user.
        self.session = requests.Session()
        self.session.verify = verify

        # uid is my Odoo user ID. db is the database I logged into.
        # Both start as None because I have not authenticated yet.
        self.uid: Optional[int] = None
        self.db: Optional[str] = None

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send an HTTP POST request to Odoo and return its JSON response."""

        # An endpoint is the URL path for an action, such as authentication.
        url = f"{self.base}{path}"

        response = self.session.post(
            url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=self.timeout,
        )

        # Then check both HTTP errors and Odoo errors.
        # A successful HTTP request can still contain an Odoo operation error.
        response.raise_for_status()
        data = cast(Dict[str, Any], response.json())

        if data.get("error"):
            raise RuntimeError(f"JSON-RPC error: {data['error']}")

        return data

    def _jsonrpc(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Put my request into the standard JSON-RPC format."""

        # JSON is the data format. RPC means Remote Procedure Call: this code asks
        # Odoo's server to run a method. params contains the inputs for that call.
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",  # Declare the JSON-RPC protocol version.
            "method": "call",  # Ask Odoo to perform a remote call.
            "params": params,   # Supply the inputs needed for that call.
            # This ID matches the response to the request; it is not a product ID.
            "id": int(time.time() * 1000) % 1_000_000,
        }

        return self._post(path, payload)

    def authenticate(
        self,
        db: str,
        login: str,
        password_or_api_key: str,
    ) -> Tuple[int, Dict[str, Any]]:
        """Log in to Odoo and keep the returned session cookie."""

        output = self._jsonrpc(
            "/web/session/authenticate",
            {
                "db": db,
                "login": login,
                "password": password_or_api_key,
            },
        )

        # A uid confirms that Odoo accepted the login.
        result = cast(Dict[str, Any], output.get("result") or {})
        uid = result.get("uid")

        if not uid:
            raise RuntimeError("Odoo authentication failed.")

        self.uid = int(uid)
        self.db = db
        return self.uid, result

    def call_kw(
        self,
        model: str,
        method: str,
        args: List[Any],
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Call a method such as fields_get, search_read, or read on a model."""

        if not self.db or self.uid is None:
            raise RuntimeError("Authenticate before calling an Odoo model.")

        # A model is a type of Odoo record, such as product.template.
        # The method is the action you want, such as fields_get, search_read, or read.
        # args are the main inputs; kwargs are named options such as fields.
        output = self._jsonrpc(
            "/web/dataset/call_kw",
            {
                "model": model,          # Example: product.template.
                "method": method,        # Example: search_read.
                "args": args,            # Positional method arguments.
                "kwargs": kwargs or {},  # Named method arguments.
            },
        )

        return output.get("result")

    def search_read(
        self,
        model: str,
        domain: List[Any],
        fields: List[str],
        limit: int = 1,
    ) -> List[Dict[str, Any]]:
        """Search Odoo and return only the fields I request."""

        return cast(
            List[Dict[str, Any]],
            self.call_kw(
                model,
                "search_read",
                [domain],
                {"fields": fields, "limit": limit},
            ),
        )

    def read(
        self,
        model: str,
        ids: Sequence[int],
        fields: List[str],
    ) -> List[Dict[str, Any]]:
        """Read fields from a record when I already know its Odoo ID."""

        return cast(
            List[Dict[str, Any]],
            self.call_kw(
                model,
                "read",
                [list(ids)],
                {"fields": fields},
            ),
        )

    def fields_get(self, model: str) -> Dict[str, Any]:
        """Return the technical definitions for every field on a model."""

        # You use fields_get to discover technical names instead of guessing them.
        # attributes limits the response to the details you need for this lesson.
        return cast(
            Dict[str, Any],
            self.call_kw(
                model,
                "fields_get",
                [],
                {
                    "attributes": [
                        "string",
                        "type",
                        "required",
                        "readonly",
                        "relation",
                        "help",
                        "selection",
                    ]
                },
            ),
        )


# ---------------------------------------------------------------------------
# 3. FIND THE SAME ONE PRODUCT
# ---------------------------------------------------------------------------

def find_product_template_id(rpc: OdooJSONRPC, sku: str) -> int:
    """Find one product template using the exact PRODUCT_SKU."""

    # A domain is an Odoo search filter. Each condition follows this format:
    # (technical_field_name, comparison_operator, value)
    domain = [("default_code", "=", sku)]

    # I use an exact match because I do not want a similar SKU by mistake.
    templates = rpc.search_read(
        TEMPLATE_MODEL,
        domain,
        ["id", "name", "default_code"],
        limit=2,
    )

    if len(templates) == 1:
        return int(templates[0]["id"])

    if len(templates) > 1:
        raise RuntimeError(
            f"More than one product.template matched SKU '{sku}'."
        )

    # Odoo may store the SKU on the variant model, product.product.
    # If the template search fails, I check the variant model as a fallback.
    variants = rpc.search_read(
        VARIANT_MODEL,
        domain,
        ["id", "default_code", "product_tmpl_id"],
        limit=2,
    )

    if not variants:
        raise RuntimeError(f"No Odoo product matched SKU '{sku}'.")

    if len(variants) > 1:
        raise RuntimeError(
            f"More than one product.product matched SKU '{sku}'."
        )

    # product_tmpl_id links the variant to its main product template.
    # Odoo returns [record_id, display_name], so you use the first value.
    template_reference = variants[0].get("product_tmpl_id")

    if not isinstance(template_reference, (list, tuple)) or not template_reference:
        raise RuntimeError("The matching variant has no usable product template ID.")

    return int(cast(int | str, template_reference[0]))


# ---------------------------------------------------------------------------
# 4. BUILD THE TECHNICAL FIELD REPORT
# ---------------------------------------------------------------------------

def choose_field_groups(field_definitions: Dict[str, Any]) -> Dict[str, List[str]]:
    """Choose the field names I will explain in the report."""

    if SHOW_ALL_FIELDS:
        # This shows every field definition returned by this Odoo database.
        # It is useful for discovery, but the output can be much longer.
        return {"All Odoo fields": sorted(field_definitions)}

    # Only return names that really exist on product.template. This means a
    # misspelled or unavailable field will be skipped instead of breaking read().
    existing_fields = [
        field_name for field_name in FIELDS_TO_SHOW if field_name in field_definitions
    ]
    return {"Selected product fields": existing_fields}


def flatten_field_groups(field_groups: Dict[str, List[str]]) -> List[str]:
    """Turn grouped field names into one unique list for the Odoo read call."""

    return list(
        dict.fromkeys(
            field_name
            for fields in field_groups.values()
            for field_name in fields
        )
    )


def choose_fields_to_pull(
    selected_fields: List[str],
    field_definitions: Dict[str, Any],
) -> List[str]:
    """Choose which selected fields should have their current values downloaded."""

    fields_to_pull: List[str] = []
    for technical_name in selected_fields:
        details = cast(Dict[str, Any], field_definitions[technical_name] or {})
        field_type = str(details.get("type") or "unknown")

        # Binary values can contain entire images or files, so this teaching
        # script shows their schema but avoids downloading the large content.
        if field_type == "binary":
            continue

        # The focused report pulls every chosen field. The full-schema report
        # avoids downloading long one2many and many2many relationship lists.
        if not SHOW_ALL_FIELDS or field_type in SAFE_FIELD_TYPES_TO_PULL:
            fields_to_pull.append(technical_name)

    return fields_to_pull


def expected_value_format(field_type: str) -> str:
    """Explain the Python/JSON value shape Odoo expects for this field type."""

    formats = {
        "boolean": "True or False",
        "integer": "A whole number, for example 5",
        "float": "A number, for example 19.99",
        "monetary": "A number, for example 19.99",
        "char": "A short text string",
        "text": "A text string",
        "html": "An HTML text string",
        "date": 'A string in "YYYY-MM-DD" format',
        "datetime": 'A string in "YYYY-MM-DD HH:MM:SS" format',
        "selection": "One allowed technical value listed in selection_options",
        "many2one": "A related record ID, or False to clear it",
        "many2many": "An Odoo command list, for example [[6, 0, [1, 2]]]",
        "one2many": "An Odoo command list, for example [[0, 0, {...}]]",
        "binary": "A base64-encoded string",
    }
    return formats.get(field_type, f"A value accepted by Odoo type '{field_type}'")


def example_update_value(field_type: str, details: Dict[str, Any]) -> Any:
    """Return a simple example value that matches the field's technical type."""

    if field_type == "selection":
        raw_selection_value: Any = details.get("selection")
        if isinstance(raw_selection_value, list) and raw_selection_value:
            raw_selection = cast(List[Any], raw_selection_value)
            first_option: Any = raw_selection[0]
            if isinstance(first_option, (list, tuple)) and first_option:
                return first_option[0]

    examples: Dict[str, Any] = {
        "boolean": True,
        "integer": 5,
        "float": 19.99,
        "monetary": 19.99,
        "char": "NEW APPROVED VALUE",
        "text": "NEW APPROVED VALUE",
        "html": "<p>NEW APPROVED VALUE</p>",
        "date": "2026-09-17",
        "datetime": "2026-09-17 12:00:00",
        "many2one": 123,
        "many2many": [[6, 0, [1, 2]]],
        "one2many": [[0, 0, {"field_name": "value"}]],
        "binary": "BASE64_ENCODED_CONTENT",
    }
    return examples.get(field_type, "VALUE ACCEPTED BY THIS FIELD")


def selection_options(details: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert Odoo selection pairs into clear technical value/label entries."""

    options: List[Dict[str, Any]] = []
    raw_selection_value: Any = details.get("selection")
    if not isinstance(raw_selection_value, list):
        return options

    raw_selection = cast(List[Any], raw_selection_value)
    for option in raw_selection:
        if isinstance(option, (list, tuple)) and len(option) >= 2:
            options.append(
                {"technical_value": option[0], "odoo_label": option[1]}
            )
    return options


def build_field_guide(
    field_definitions: Dict[str, Any],
    product: Dict[str, Any],
    field_groups: Dict[str, List[str]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Place each selected field's definition next to its current value."""

    field_guide: Dict[str, List[Dict[str, Any]]] = {}
    for group_name, technical_names in field_groups.items():
        group_fields: List[Dict[str, Any]] = []

        for technical_name in technical_names:
            details = cast(Dict[str, Any], field_definitions[technical_name] or {})
            field_type = str(details.get("type") or "unknown")
            readonly = bool(details.get("readonly", False))
            writable = not readonly

            field_report: Dict[str, Any] = {
                # This is the exact field key you use in Python and JSON-RPC.
                "technical_name": technical_name,
                # This is the human-readable label shown inside Odoo.
                "odoo_label": details.get("string"),
                "field_type": field_type,
                "current_value": (
                    product.get(technical_name)
                    if technical_name in product
                    else f"[value not downloaded: {field_type}]"
                ),
                "required": bool(details.get("required", False)),
                "readonly": readonly,
                # False means Odoo's model marks the field as writable.
                # The logged-in user's access rights must still allow the change.
                "model_marks_writable": writable,
                "relation": details.get("relation"),
                "help": details.get("help"),
                "expected_value_format": expected_value_format(field_type),
                "selection_options": selection_options(details),
            }

            # This is the dictionary shape you would place inside an Odoo write
            # call. The script only displays it and never sends it to Odoo.
            if writable:
                field_report["update_values_example"] = {
                    technical_name: example_update_value(field_type, details)
                }

            group_fields.append(field_report)

        field_guide[group_name] = group_fields

    return field_guide


def group_current_values(
    product: Dict[str, Any],
    field_groups: Dict[str, List[str]],
) -> Dict[str, Dict[str, Any]]:
    """Display this product's values in the same easy-to-follow groups."""

    return {
        group_name: {
            technical_name: product.get(technical_name, "[value not downloaded]")
            for technical_name in technical_names
        }
        for group_name, technical_names in field_groups.items()
    }


def short_terminal_value(value: Any, maximum_length: int = 70) -> str:
    """Keep long Odoo values from taking over the terminal."""

    if value is False or value is None or value == "":
        return "[blank]"

    text = " ".join(str(value).split())
    if len(text) > maximum_length:
        return text[: maximum_length - 3] + "..."
    return text


def print_compact_field_report(
    field_definitions: Dict[str, Any],
    product: Dict[str, Any],
    selected_fields: List[str],
) -> None:
    """Print one short teaching row for each selected product field."""

    print("\n" + "=" * 100)
    print("PRODUCT FIELDS")
    print("=" * 100)
    print(f"{'TECHNICAL NAME':<36} {'TYPE':<12} {'EDITABLE':<10} CURRENT VALUE")
    print("-" * 100)

    for technical_name in selected_fields:
        details = cast(Dict[str, Any], field_definitions[technical_name] or {})
        field_type = str(details.get("type") or "unknown")
        editable = "yes" if not bool(details.get("readonly", False)) else "no"
        current_value = short_terminal_value(
            product.get(technical_name, "[not downloaded]")
        )
        print(
            f"{technical_name:<36} {field_type:<12} {editable:<10} "
            f"{current_value}"
        )

    print("-" * 100)
    print("Technical name = the exact key you use in a JSON-RPC read or write call.")
    print("Editable = Odoo's model allows writes; your user permissions must also allow it.")


def print_json(title: str, value: Any) -> None:
    """Print Python dictionaries and lists as readable technical JSON."""

    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# 5. CONFIGURATION CHECK
# ---------------------------------------------------------------------------

def validate_configuration() -> None:
    """Check the same required Odoo settings before making any API calls."""

    required_settings = {
        "ODOO_BASE": ODOO_BASE,
        "ODOO_DB": ODOO_DB,
        "OD_EMAIL": ODOO_EMAIL,
        "OD_PASS": ODOO_PASS,
        "PRODUCT_SKU": PRODUCT_SKU,
    }

    missing = [name for name, value in required_settings.items() if not value]
    if missing:
        raise RuntimeError(
            "Missing required .env values: " + ", ".join(missing)
        )


# ---------------------------------------------------------------------------
# 6. MAIN READ-ONLY WORKFLOW
# ---------------------------------------------------------------------------

def main() -> None:
    """Log in, find the selected product, and explain its field structure."""

    # Then check the same .env settings used by odoo_read_and_write.py.
    validate_configuration()

    # Create the client and log in. Later calls reuse this same session.
    rpc = OdooJSONRPC(
        ODOO_BASE,
        verify=VERIFY_SSL,
        timeout=ODOO_TIMEOUT,
    )
    uid, _session_info = rpc.authenticate(ODOO_DB, ODOO_EMAIL, ODOO_PASS)
    print(f"Authenticated to Odoo successfully as user ID {uid}.")
    print(f"Using PRODUCT_SKU from .env: {PRODUCT_SKU}")

    # This finds the one product selected by PRODUCT_SKU in the shared .env file.
    # At this point you have only read data; you have not updated anything.
    template_id = find_product_template_id(rpc, PRODUCT_SKU)

    # fields_get returns the technical schema for product.template.
    # This tells you which field names exist and how Odoo expects them formatted.
    field_definitions = rpc.fields_get(TEMPLATE_MODEL)
    field_groups = choose_field_groups(field_definitions)
    selected_fields = flatten_field_groups(field_groups)
    fields_to_pull = choose_fields_to_pull(selected_fields, field_definitions)

    missing_requested_fields = [
        field_name for field_name in FIELDS_TO_SHOW if field_name not in field_definitions
    ]
    if missing_requested_fields:
        print(
            "Skipped fields that do not exist on product.template: "
            + ", ".join(missing_requested_fields)
        )

    # Now you run that read request and pull the current product data.
    product_rows = rpc.read(TEMPLATE_MODEL, [template_id], fields_to_pull)

    if not product_rows:
        raise RuntimeError(f"Product template {template_id} could not be read.")

    product = product_rows[0]

    # The normal output is one compact row per field. This avoids printing the
    # same product information several times and keeps the demo easy to follow.
    print_compact_field_report(field_definitions, product, selected_fields)

    print("\nREAD CALL FORMAT")
    print(f'  model = "{TEMPLATE_MODEL}"')
    print('  method = "read"')
    print(f"  args = [[{template_id}]]")
    print(f"  kwargs = {{\"fields\": {fields_to_pull!r}}}")

    # Turn SHOW_TECHNICAL_DETAILS on near the top of this file when you want the
    # longer schema explanation. It stays off during the normal presentation.
    field_guide = build_field_guide(field_definitions, product, field_groups)
    editable_fields = {
        group_name: [
            field for field in fields if field["model_marks_writable"] is True
        ]
        for group_name, fields in field_guide.items()
    }
    editable_fields = {
        group_name: fields
        for group_name, fields in editable_fields.items()
        if fields
    }

    if SHOW_TECHNICAL_DETAILS:
        print_json("DETAILED TECHNICAL FIELD GUIDE", field_guide)
        print_json("DETAILED EDITABLE FIELD GUIDE", editable_fields)

    # This is the exact technical shape of a future write request.
    # You only print this dictionary. You never pass it to rpc.call_kw.
    example_field = (
        "meta_description"
        if "meta_description" in field_definitions
        else next(
            (
                str(field["technical_name"])
                for fields in editable_fields.values()
                for field in fields
                if field["field_type"] in {"char", "text", "html"}
            ),
            "technical_field_name",
        )
    )

    write_request_example: Dict[str, Any] = {
        "endpoint": "/web/dataset/call_kw",
        "model": TEMPLATE_MODEL,
        "method": "write",
        "args": [
            [template_id],
            {example_field: "NEW APPROVED VALUE WOULD GO HERE"},
        ],
        "kwargs": {},
        "warning": "FORMAT EXAMPLE ONLY — THIS REQUEST WAS NOT SENT",
    }
    print_json("ONE WRITE FORMAT EXAMPLE — NOT EXECUTED", write_request_example)

    print("\nFinished. This script only pulled and displayed Odoo information.")
    print("Set SHOW_TECHNICAL_DETAILS = True if you need the longer field guide.")
    print("Set SHOW_ALL_FIELDS = True if you need to discover every Odoo field.")


# This runs main() only when you execute this file directly.
# Importing its functions somewhere else will not automatically call Odoo.
if __name__ == "__main__":
    main()
