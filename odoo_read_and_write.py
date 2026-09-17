"""A small example of how I read and update Odoo through JSON-RPC.

I use Odoo JSON-RPC to log in, find one product by SKU, read its current data,
and update an approved field. I use Anthropic separately to suggest the new
text. The AI only returns a suggestion; my Python code decides whether to send
that value to Odoo.

Run ``python3 main.py`` to preview. Run ``python3 main.py --apply`` only after
you are ready to review and publish a real change.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast

import requests
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# 1. LOAD CONFIGURATION
# ---------------------------------------------------------------------------

# First keep api keys and passwords in .env instead of hard-coding them here.
load_dotenv()

# ODOO_BASE tells my script where the Odoo server is.
# ODOO_DB tells Odoo which database I want to use on that server.
# rstrip("/") removes a final slash so my endpoint URLs are formed correctly.
ODOO_BASE = os.getenv("ODOO_BASE", "").strip().rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "").strip()

# I use these credentials to authenticate. 
# The script only gets the permissions already assigned to this user.

ODOO_EMAIL = (
    os.getenv("OD_EMAIL","").strip()
)
ODOO_PASS = (
    os.getenv("OD_PASS","").strip()
)

# VERIFY_SSL checks the server's HTTPS certificate; you normally leave it true.
# ODOO_TIMEOUT is how many seconds you wait for an Odoo response.
VERIFY_SSL = os.getenv("VERIFY_SSL", "true").strip().lower() == "true"
ODOO_TIMEOUT = int(os.getenv("ODOO_TIMEOUT", "30"))

# You choose the product by changing PRODUCT_SKU in .env.
# This handles one SKU per run so the target is always clear.
PRODUCT_SKU = os.getenv("PRODUCT_SKU", "").strip()

# I use Anthropic for the AI portion of this example. You can replace this
# provider later without changing how the Odoo JSON-RPC calls work.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_API_BASE = os.getenv(
    "ANTHROPIC_API_BASE",
    "https://api.anthropic.com/v1",
).strip().rstrip("/")

# EDITABLE_FIELD is the technical field name inside Odoo.
# The allowlist prevents this script from writing to an unexpected field.
# If you change the target field, verify its name and add it to the allowlist.
EDITABLE_FIELD = "meta_description"
ALLOWED_UPDATE_FIELDS = {EDITABLE_FIELD}

# This is the one instruction I send to the AI.
# If you want the AI to perform a different task, this is what you change.
# This can be as specific or broad as you would like.
AI_INSTRUCTIONS = (
    "Write one natural SEO-friendly meta description using only the supplied "
    "product name and SKU. Use the product name exactly once. Do not repeat the "
    "SKU separately if it already appears in the product name. Do not interpret "
    "individual words in the product name as additional features. Do not invent "
    "uses, materials, specifications, compatibility, quality claims, or performance "
    "claims. End with a neutral invitation to view product details. Aim for 120 "
    "to 150 characters and never exceed 155 characters. Return only the final text."
)

# AI does not follow instructions perfectly every time. This checks the result in
# Python and allow up to three attempts to meet the character limit.
MAX_META_DESCRIPTION_LENGTH = 155
AI_GENERATION_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# 2. ODOO JSON-RPC CLIENT
# ---------------------------------------------------------------------------

class OdooJSONRPC:
    """I keep all of my Odoo connection methods in this class."""

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
        data = response.json()

        if data.get("error"):
            raise RuntimeError(f"JSON-RPC error: {data['error']}")

        return cast(Dict[str, Any], data)

    def _jsonrpc(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Put my request into the standard JSON-RPC format."""

        # JSON is the data format. RPC means Remote Procedure Call: this code asks
        # Odoo's server to run a method. params contains the inputs for that call.
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",  # Declare the JSON-RPC protocol version.
            "method": "call",  # Ask Odoo to perform a remote call.
            "params": params,   # Inputs needed for that call.
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
        """Call a method such as search_read, read, or write on an Odoo model."""

        if not self.db or self.uid is None:
            raise RuntimeError("Authenticate before calling an Odoo model.")

        # A model is a type of Odoo record, such as product.template.
        # The method is the action you want, such as search_read or write.
        # args are the main inputs; kwargs are named options such as fields.
        output = self._jsonrpc(
            "/web/dataset/call_kw",
            {
                "model": model,          # Example: product.template.
                "method": method,        # Example: search_read or write.
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

        # A domain is an Odoo search filter. This example means:
        # "Find the record whose default_code exactly equals ABC-123."
        return cast(
            List[Dict[str, Any]],
            self.call_kw(
                model,
                "search_read",
                [domain],
                {
                    "fields": fields,
                    "limit": limit,
                    "order": "id desc",
                },
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

    def write(
        self,
        model: str,
        ids: Sequence[int],
        values: Dict[str, Any],
    ) -> bool:
        """Send approved field changes to an existing Odoo record."""

        return bool(
            self.call_kw(
                model,
                "write",
                [list(ids), values],
                {},
            )
        )


# ---------------------------------------------------------------------------
# 3. PRODUCT LOOKUP
# ---------------------------------------------------------------------------

def find_product_by_sku(rpc: OdooJSONRPC, sku: str) -> Dict[str, Any]:
    """Find one product template using an exact SKU."""

    fields = ["id", "name", "default_code", EDITABLE_FIELD]

    # I use an exact match because I do not want to update a similar SKU by mistake.
    templates = rpc.search_read(
        "product.template",
        [("default_code", "=", sku)],
        fields,
        limit=2,
    )

    if len(templates) == 1:
        return templates[0]

    if len(templates) > 1:
        raise RuntimeError(
            f"More than one product.template matched SKU '{sku}'. Nothing was changed."
        )

    # Odoo may store the SKU on the variant model, product.product.
    # If the template search fails, check the variant model as a fallback.
    variants = rpc.search_read(
        "product.product",
        [("default_code", "=", sku)],
        ["id", "default_code", "product_tmpl_id"],
        limit=2,
    )

    if not variants:
        raise RuntimeError(f"No Odoo product matched SKU '{sku}'.")

    if len(variants) > 1:
        raise RuntimeError(
            f"More than one product.product matched SKU '{sku}'. Nothing was changed."
        )

    # product_tmpl_id links the variant to its product template.
    # Odoo returns [record_id, display_name], so you use the first value.
    template_reference = variants[0].get("product_tmpl_id")

    if not isinstance(template_reference, (list, tuple)) or not template_reference:
        raise RuntimeError("The matching variant has no usable product template ID.")

    template_id = int(cast(int | str, template_reference[0]))
    templates = rpc.read("product.template", [template_id], fields)

    if not templates:
        raise RuntimeError(f"Product template {template_id} could not be read.")

    return templates[0]


# ---------------------------------------------------------------------------
# 4. ANTHROPIC CONTENT PROPOSAL
# ---------------------------------------------------------------------------

def generate_meta_description(product: Dict[str, Any]) -> str:
    """Request a text proposal from Anthropic without writing to Odoo."""

    # Only send the product information the AI needs.
    # You do not send your Odoo password, API key, or session cookie.
    user_payload = {
        "product_name": str(product.get("name") or ""),
        "sku": PRODUCT_SKU,
        "current_meta_description": str(product.get(EDITABLE_FIELD) or ""),
    }

    previous_attempt = ""

    # This request goes to Anthropic, not Odoo.
    # Anthropic returns text but has no access to my Odoo session.
    for attempt_number in range(1, AI_GENERATION_ATTEMPTS + 1):
        request_payload = dict(user_payload)

        # If the previous result was too long, ask the AI to rewrite it.
        # You do this instead of cutting the text in the middle of a sentence.
        if previous_attempt:
            request_payload["previous_attempt"] = previous_attempt
            request_payload["revision_request"] = (
                "Rewrite the previous attempt so it is no more than "
                f"{MAX_META_DESCRIPTION_LENGTH} characters. Return only the revision."
            )

        response = requests.post(
            f"{ANTHROPIC_API_BASE}/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "max_tokens": 200,
                "temperature": 0.2,
                # This is the single AI instruction defined near the top.
                "system": AI_INSTRUCTIONS,
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(request_payload, ensure_ascii=False),
                    }
                ],
            },
            timeout=LLM_TIMEOUT,
        )

        if not response.ok:
            raise RuntimeError(
                f"Anthropic API error {response.status_code}: {response.text}"
            )

        data = cast(Dict[str, Any], response.json())
        text_parts: List[str] = []

        # Anthropic returns content blocks. You keep only the text blocks.
        for item in cast(List[Dict[str, Any]], data.get("content") or []):
            if isinstance(item, dict) and item.get("type") == "text":
                value = item.get("text")
                if isinstance(value, str) and value.strip():
                    text_parts.append(value.strip())

        proposed_value = "\n".join(text_parts).strip()

        if not proposed_value:
            raise RuntimeError("Anthropic returned no usable text.")

        # The prompt requests the limit; this Python check actually enforces it.
        if len(proposed_value) <= MAX_META_DESCRIPTION_LENGTH:
            return proposed_value

        print(
            f"AI attempt {attempt_number} returned {len(proposed_value)} characters; "
            f"requesting a revision under {MAX_META_DESCRIPTION_LENGTH}."
        )
        previous_attempt = proposed_value

    raise RuntimeError(
        "Anthropic could not produce a meta description within the "
        f"{MAX_META_DESCRIPTION_LENGTH}-character limit after "
        f"{AI_GENERATION_ATTEMPTS} attempts. Nothing was written to Odoo."
    )


# ---------------------------------------------------------------------------
# 5. SAFETY AND CONFIGURATION VALIDATION
# ---------------------------------------------------------------------------

def validate_configuration() -> None:
    """Check required settings before I call Odoo or Anthropic."""

    required_settings = {
        "ODOO_BASE": ODOO_BASE,
        "ODOO_DB": ODOO_DB,
        "OD_EMAIL/ODOO_EMAIL": ODOO_EMAIL,
        "OD_PASS/ODOO_PASSWORD": ODOO_PASS,
        "PRODUCT_SKU": PRODUCT_SKU,
        "LLM_MODEL": LLM_MODEL,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    }

    missing = [name for name, value in required_settings.items() if not value]

    if missing:
        raise RuntimeError(
            "Missing required .env values: " + ", ".join(missing)
        )

    if LLM_PROVIDER != "anthropic":
        raise RuntimeError(
            "This beginner starter currently supports only LLM_PROVIDER=anthropic."
        )

    # If you change EDITABLE_FIELD but forget the allowlist, it stops before writing.
    if EDITABLE_FIELD not in ALLOWED_UPDATE_FIELDS:
        raise RuntimeError(f"Field '{EDITABLE_FIELD}' is not allowlisted.")


def print_preview(product: Dict[str, Any], proposed_value: str) -> None:
    """Show the current value and the proposed value before any update."""

    current_value = str(product.get(EDITABLE_FIELD) or "")

    print("\n" + "=" * 68)
    print("PREVIEW ONLY")
    print("=" * 68)
    print(f"Product ID: {product.get('id')}")
    print(f"Product:    {product.get('name') or ''}")
    print(f"SKU:        {PRODUCT_SKU}")
    print(f"Field:      {EDITABLE_FIELD}")
    print("\nBEFORE")
    print(current_value or "[blank]")
    print("\nAFTER")
    print(proposed_value)
    print("=" * 68)


# ---------------------------------------------------------------------------
# 6. MAIN PREVIEW / APPLY WORKFLOW
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the full login, lookup, AI, preview, and update process."""

    parser = argparse.ArgumentParser(
        description="Preview one AI-assisted Odoo update through JSON-RPC."
    )

    # This is preview mode and does not write:
    #     python3 main.py
    # This enables the confirmation and write steps:
    #     python3 main.py --apply
    # Even with --apply, you still require the word APPLY before writing.
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Permit publishing after displaying the preview and asking again.",
    )

    args = parser.parse_args()

    # Then check the configuration first so missing values produce a clear error.
    validate_configuration()

    # Create the client and log in. Later Odoo calls reuse this same session.
    rpc = OdooJSONRPC(
        ODOO_BASE,
        verify=VERIFY_SSL,
        timeout=ODOO_TIMEOUT,
    )
    uid, _session_info = rpc.authenticate(ODOO_DB, ODOO_EMAIL, ODOO_PASS)
    print(f"Authenticated to Odoo successfully as user ID {uid}.")

    # This finds one product and save its current value.
    # At this point you have only read data; you have not updated anything.
    product = find_product_by_sku(rpc, PRODUCT_SKU)
    product_id = int(product["id"])
    approved_before = str(product.get(EDITABLE_FIELD) or "")

    # You ask AI for a suggestion and display it next to the current value.
    # AI writes the draft; your code controls whether it is published.
    proposed_value = generate_meta_description(product)
    print_preview(product, proposed_value)

    # Without --apply, you stop here. 
    if not args.apply:
        print("\nNothing was written to Odoo.")
        print("Review the preview, then rerun with --apply only if approved.")
        return

    # In apply mode, you still require confirmation after showing the preview.
    confirmation = input(
        "\nType APPLY to publish this exact preview, or press Enter to cancel: "
    )

    if confirmation != "APPLY":
        print("Cancelled. Nothing was written to Odoo.")
        return

    # Read the field again before writing. If someone changed it after my
    # preview, you stop. This is called a stale-write check.
    fresh_rows = rpc.read(
        "product.template",
        [product_id],
        ["id", EDITABLE_FIELD],
    )

    if not fresh_rows:
        raise RuntimeError("The product disappeared after preview. Nothing was written.")

    current_value = str(fresh_rows[0].get(EDITABLE_FIELD) or "")

    if current_value != approved_before:
        raise RuntimeError(
            "The Odoo value changed after the preview. Nothing was written; "
            "run a fresh preview and review it again."
        )

    # This is the actual push to Odoo: one record ID, one approved field,
    # and the exact value shown in the preview.
    updated = rpc.write(
        "product.template",
        [product_id],
        {EDITABLE_FIELD: proposed_value},
    )

    if not updated:
        raise RuntimeError("Odoo did not confirm the update.")

    # You read the field one last time to verify that Odoo saved the exact value.
    verified_rows = rpc.read(
        "product.template",
        [product_id],
        ["id", EDITABLE_FIELD],
    )
    verified_value = (
        str(verified_rows[0].get(EDITABLE_FIELD) or "")
        if verified_rows
        else ""
    )

    if verified_value != proposed_value:
        raise RuntimeError(
            "Odoo reported success, but the saved value could not be verified."
        )

    print("Update completed and the saved value was verified.")


# This runs main() only when you execute this file directly.
# Importing its functions somewhere else will not automatically call the APIs.
if __name__ == "__main__":
    main()

