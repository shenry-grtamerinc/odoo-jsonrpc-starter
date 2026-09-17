# Odoo JSON-RPC Starter

This small project shows how I use Python and JSON-RPC to connect to Odoo,
authenticate a user, find one product by SKU, read product information, and
safely update one approved field.

It is designed as a teaching example. The code includes comments explaining
the technical pieces without requiring someone to understand the larger
production application first.

## What JSON-RPC does

JSON-RPC stands for **JavaScript Object Notation Remote Procedure Call**.

In this project, my Python code sends a JSON request asking the Odoo server to
run a method. Odoo runs that method using the permissions of the authenticated
user and returns the result as JSON.

The basic flow is:

1. Load the connection settings from `.env`.
2. Authenticate to Odoo and receive a user ID called the `uid`.
3. Select an Odoo model, such as `product.template`.
4. Call a method, such as `search_read`, `read`, `fields_get`, or `write`.
5. Pass the method's arguments and keyword arguments.
6. Receive the result from Odoo as JSON data.

JSON-RPC does not bypass Odoo security. The scripts only receive the access
already assigned to the Odoo account used for authentication.

## Files

### `odoo_pull_product_data.py`

This is the read-only teaching script. It:

- authenticates to Odoo;
- finds exactly one product using `PRODUCT_SKU` from `.env`;
- checks both `product.template` and `product.product` when necessary;
- uses `fields_get` to inspect Odoo's technical field definitions;
- reads the selected product fields;
- shows whether Odoo marks each field as editable; and
- prints an example of a correctly formatted write request without sending it.

This script does **not** write, create, or delete records.

### `odoo_read_and_write.py`

This is the controlled update example. It:

- authenticates to Odoo;
- finds one product by its exact SKU;
- reads the current `meta_description`;
- asks Anthropic to propose a new value;
- validates the generated character length;
- displays a before-and-after preview;
- requires both `--apply` and typed confirmation before writing;
- checks that the record did not change after the preview; and
- reads the record again to verify the saved value.

Running the script normally is preview-only. Nothing is written unless the
apply workflow is deliberately used.

### `requirements.txt`

The project uses:

- `requests` for HTTP requests; and
- `python-dotenv` for loading the local `.env` file.

## Setup

### 1. Create a virtual environment

```bash
python3 -m venv .venv
```

### 2. Activate it

On macOS or Linux:

```bash
source .venv/bin/activate
```

### 3. Install the requirements

```bash
python3 -m pip install -r requirements.txt
```

### 4. Deactivate it when finished

```bash
deactivate
```

The virtual environment only selects this project's Python interpreter and
packages. It does not normally consume CPU by itself.

## How I found the Odoo database name

While logged into Odoo, I right-clicked the page, selected **Inspect**, and opened the **Console** tab.

I pasted this JSON-RPC request into the Console and pressed **Enter**:

```javascript
fetch("/web/session/get_session_info", {
  method: "POST",
  headers: {
    "Content-Type": "application/json"
  },
  body: JSON.stringify({
    jsonrpc: "2.0",
    method: "call",
    params: {},
    id: 1
  })
})
  .then(response => response.json())
  .then(data => console.log(data.result.db));
```

The Console returned:

```text
greatamerican.steersman.io
```

I then added that value to `.env`:

```env
ODOO_DB=greatamerican.steersman.io
```

This request reads the database name from the current Odoo session. It does not update or delete any Odoo data.

## Environment variables

Create a local `.env` file in the same project folder:
Reference `.env.example` for how to create this.

Never upload the real `.env` file to GitHub. Store only placeholder variable
names in `.env.example`.

## Read one product

Set `PRODUCT_SKU` in `.env`, then run:

```bash
python3 odoo_pull_product_data.py
```

The script displays a compact list of selected fields. To change which fields
appear, edit `FIELDS_TO_SHOW` inside `odoo_pull_product_data.py`:

```python
FIELDS_TO_SHOW = [
    "id",
    "name",
    "default_code",
    "meta_title",
    "meta_description",
    "list_price",
]
```

These must be Odoo's technical field names. The script includes commented
examples that can be enabled by removing the `#`.

For a longer technical report, change:

```python
SHOW_TECHNICAL_DETAILS = True
```

To discover every available field, change:

```python
SHOW_ALL_FIELDS = True
```

The full-field option can produce a large terminal report, so it remains off
for the normal demonstration.

## Preview an update

Run:

```bash
python3 odoo_read_and_write.py
```

The script displays the current value and proposed value, then stops without
writing to Odoo.

## Apply an approved update

After reviewing the preview, run:

```bash
python3 odoo_read_and_write.py --apply
```

`--apply` is an argument passed to the Python script. It is not a separate
terminal command. The script still requires typed confirmation before it sends
the update to Odoo.

## Technical request format

The JSON-RPC message uses an envelope like this:

```python
payload = {
    "jsonrpc": "2.0",
    "method": "call",
    "params": params,
    "id": request_id,
}
```

An Odoo model call identifies the model, method, arguments, and keyword
arguments:

```python
{
    "model": "product.template",
    "method": "read",
    "args": [[product_id]],
    "kwargs": {
        "fields": ["name", "default_code", "meta_description"]
    },
}
```

A write request follows this structure:

```python
{
    "model": "product.template",
    "method": "write",
    "args": [
        [product_id],
        {"meta_description": "NEW APPROVED VALUE"},
    ],
    "kwargs": {},
}
```

The first list contains the record IDs. The second dictionary maps each
technical field name to its new value.

## Learning references

### Written JSON-RPC guide

[Cybrosys — Odoo 15 Development Book: JSON-RPC](https://www.cybrosys.com/odoo/odoo-books/odoo-15-development/ch14/json-rpc/)

This article explains the JSON-RPC request structure and gives examples of
connecting to Odoo and reading, creating, updating, and deleting records. It
uses Odoo 15 and the classic `/jsonrpc` service format, while this project uses
Odoo's web-session endpoints. The core ideas—authentication, models, methods,
record IDs, and arguments—are still directly relevant.

### Video explanation

[Odoo XML-RPC and JSON-RPC from inside — start at 42:19](https://www.youtube.com/watch?v=Ohb4JEjLkBQ&t=2539s)

Only the section from **42:19 to 51:13** is needed for this project. It shows:

- how the JSON-RPC helper creates the payload and headers;
- the authentication call;
- the request that performs `search_read`;
- how Odoo routes `/jsonrpc` calls through its controller and dispatcher;
- how model, method, domain, and field arguments reach Odoo; and
- how the result returns as a list of dictionaries.

The key conclusion is that JSON-RPC and XML-RPC use different data formats,
but Odoo routes them to the same underlying models and methods.

