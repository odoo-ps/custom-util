> [!WARNING]
> This repository is for internal usage only. It will be eventually merged into [odoo/upgrade-util](https://github.com/odoo/upgrade-util). No support will be provided for this code and external contributions will not be accepted.



# 🧙‍🔧 PS-Tech Upgrade Custom Utils

This repository contains helper functions to facilitate the writing of upgrade scripts, specifically tailored towards custom Odoo modules.

> If you are converting studio or saas modules, you are probably in the right place here.

## Installation

### Through odoo-bin
Once you have clone this repository locally, just start `odoo` with the `src` directory of this repo added to the `--upgrade-path` option.
```shell-session
$ ./odoo-bin --upgrade-path=/path/to/custom-util/src,/path/to/other/upgrade/script/directory [...]
```

### As a python package
On platforms where you dont manage odoo yourself, you can install this package via pip:
```shell-session
$ python3 -m pip install git+https://github.com/odoo-ps/custom-util@master
```
On [Odoo.sh](https://www.odoo.sh/) it is recommended to add it to the `requirements.txt` of your repository:
```
odoo_upgrade_custom_util @ git+https://github.com/odoo-ps/custom-util@master
```
## How to use them?
Once installed, the helpers are available in the `custom_util` package under the `odoo.upgrade` namespace. For example:
```py
from odoo.upgrade import custom_util

def migrate(cr, version):
    custom_util.edit_views(...)  # etc.
```

## Usage guide

### Overview

Odoo upgrade scripts come in three flavours, each running at a different point in the upgrade
lifecycle. Knowing which to use for each task is the most important decision when writing a
migration script.

| Script type | When it runs | ORM available? | Typical use |
|---|---|---|---|
| `pre-migrate.py` | Before the module is upgraded | No — raw SQL only | Rename models/fields, rename xmlids, SQL-level data fixes |
| `post-migrate.py` | After the module is upgraded | Yes | Fix indirect references, patch views, ORM-level data fixes |
| `end-migrate.py` | After **all** modules are upgraded | Yes (full registry) | Studio view creation/update, anything requiring `web_studio` |

#### Typical workflow for a field rename

The most common pattern across migration scripts is: rename in `pre-`, then fix up all indirect
references in `post-`:

```
pre-migrate.py                         post-migrate.py
──────────────────────────────         ──────────────────────────────────────
custom_rename_field(...)               do_pending_refactors(cr)
custom_rename_field(...)               edit_views(cr, {...})
rename_xmlids(...)                     add_view_modifications_to_migration_reports(...)
transfer_custom_fields(...)
```

`custom_rename_field` queues every rename into `FIELD_RENAMES_PENDING`. When
`do_pending_refactors` is called in `post-` or at the end of 'pre-', it flushes that queue and updates all indirect
references in server actions, mail templates, and other records that embed field names as text.

#### Migration reports

`add_view_modifications_to_migration_reports` appends an entry to the upgrade migration report —
a document visible to the PS consultant after the upgrade. Use it whenever you patch a Studio or
website view so the consultant knows what changed and can verify it manually. Call it in
`post-migrate.py` after the `edit_views` calls.

#### Method placement summary

| Method | `pre-` | `post-` | `end-` | Notes |
|---|:---:|:---:|:---:|---|
| **Renaming** | | | | |
| `custom_rename_model` | ✓ | | | Must run before field renames on that model |
| `custom_rename_field` | ✓ | | | Queues rename for `do_pending_refactors` |
| `custom_rename_module` | ✓ | | | |
| `transfer_custom_fields` | ✓ | | | |
| `rename_xmlids` | ✓ | | | |
| `update_related_field` | ✓ | | | |
| `update_custom_views` | ✓ | | | Simple text-replace fallback; prefer `edit_views` in post- |
| **Post-rename fixups** | | | | |
| `do_pending_refactors` | | ✓ | | Must run after all `custom_rename_field` calls |
| `fix_renames_in_fields` | | ✓ | | Lower-level alternative to `do_pending_refactors` |
| `fix_renames_in_records` | | ✓ | | Target a specific model |
| `rename_in_translation` | | ✓ | | |
| **View editing** | | | | |
| `edit_views` | | ✓ | | Patch Studio / custom views after the upgrade |
| `edit_website_views` | | ✓ | | Requires website ORM |
| `activate_views` | ✓ | ✓ | | Raw SQL, works in either |
| `deactivate_views` | ✓ | ✓ | | Raw SQL, works in either |
| `set_studio_view` | | | ✓ | Requires `web_studio` in registry |
| `reset_studio_view_priority` | | | ✓ | Requires ORM + `web_studio` |
| **View utilities** | | | | |
| `get_views_ids` | ✓ | ✓ | | Raw SQL |
| `get_website_views_ids` | | ✓ | | Requires ORM |
| `get_arch` | ✓ | ✓ | | Raw SQL |
| `extract_elements` | ✓ | ✓ | | Pure Python |
| `extract_elements_from_view` | ✓ | ✓ | | Raw SQL |
| `create_cow_views` | | ✓ | | Requires website ORM |
| `create_cow_view` | | ✓ | | Requires website ORM |
| **Data migration** | | | | |
| `merge_model_and_data` | ✓ | | | SQL-level, run before ORM loads the new model |
| `merge_groups` | | ✓ | | Requires ORM |
| **Dashboard** | | | | |
| `remove_broken_dashboard_actions` | | ✓ | | Requires ORM |
| `cleanup_old_dashboards` | | ✓ | | Raw SQL, but logically a post- task |
| **Module / record utilities** | | | | |
| `modules_already_installed` | ✓ | ✓ | | Raw SQL, safe anywhere |
| `set_not_imported_modules` | ✓ | ✓ | | Raw SQL, safe anywhere |
| `toggle_active` | ✓ | ✓ | | Raw SQL, safe anywhere |
| `get_ids` | ✓ | ✓ | | Raw SQL, safe anywhere |
| `get_migscript_module` | ✓ | ✓ | | Pure Python, safe anywhere |
| `expand_studio_xmlids` | ✓ | ✓ | | Pure Python, safe anywhere |
| `get_existing_models_fields` | ✓ | ✓ | | Raw SQL, safe anywhere |
| `get_model_xmlid_basename` | ✓ | ✓ | | Pure Python, safe anywhere |
| `build_chained_replace` | ✓ | ✓ | | Pure Python, safe anywhere |
| `indent_tree` | ✓ | ✓ | | Pure Python, safe anywhere |
| **Reporting** | | | | |
| `add_view_modifications_to_migration_reports` | | ✓ | | Call after `edit_views` |

#### Script skeleton

Below is a commented skeleton showing the typical structure of a complete migration. Not every
section will be needed for every module — omit what does not apply.

**`pre-migrate.py`**
```py
from odoo.upgrade import custom_util


def migrate(cr, version):
    # 1. Rename custom models (before fields, so the table exists under the new name)
    custom_util.custom_rename_model(cr, "x_old_model", "new.model")

    # 2. Rename fields on standard and custom models
    custom_util.custom_rename_field(cr, "sale.order", "x_studio_note", "custom_note")
    custom_util.custom_rename_field(cr, "res.partner", "x_vip_client", "is_vip")

    # 3. Rename external identifiers
    custom_util.rename_xmlids(cr, [
        ("old_xmlid", "new_xmlid"),
        ("old_module.record", "new_module.record"),
    ])

    # 4. Move/rename Studio fields into the proper technical module
    custom_util.transfer_custom_fields(cr, "studio_customization", "my_module", [
        ("sale.order", "x_studio_note"),      # becomes "note" (prefix stripped)
        ("res.partner", "x_vip_client", "is_vip"),
    ])

    # 5. Rename a custom module if needed
    custom_util.custom_rename_module(cr, "old_module_name", "new_module_name")
```

**`post-migrate.py`**
```py
import os.path as osp

from odoo.upgrade import custom_util
from custom_util import (
    do_pending_refactors,
    RemoveFields, RenameElements, UpdateAttributes, AddElements,
    add_view_modifications_to_migration_reports,
)


def migrate(cr, version):
    # 1. Fix all indirect references for the field renames done in pre-
    #    (server actions, mail templates, related fields, etc.)
    do_pending_refactors(cr)

    # 2. Patch views that reference renamed / removed fields
    view_ops = {
        "my_module.view_order_form_custom": [
            RenameElements("x_studio_note", "custom_note"),
            RemoveFields("x_obsolete_field"),
        ],
        "odoo_studio_sale_ord_a1b2c3d4": [
            UpdateAttributes('//field[@name="date_order"]', invisible="1"),
            AddElements('//field[@name="partner_id"]', '<field name="ref"/>', position="after"),
        ],
    }
    custom_util.edit_views(cr, view_ops)

    # 3. Log the view patches to the migration report for consultant review
    add_view_modifications_to_migration_reports(cr, view_ops)

    # 4. Other data fixes
    custom_util.merge_groups(cr, "my_module.group_old", "my_module.group_new")
    custom_util.set_not_imported_modules(cr, ["my_module"])
```

**`end-migrate.py`** *(only needed when Studio views must be created/updated)*
```py
import os.path as osp
from odoo.upgrade import custom_util


def migrate(cr, version):
    custom_util.set_studio_view(
        cr,
        path=osp.join(osp.dirname(__file__), "studio_customization.xml"),
        inherit_xml_id="sale.view_order_form",
    )
```

---

### Model renaming

`custom_rename_model(cr, old, new)` — rename a custom model. Sets the model state to `base`
before delegating to `util.rename_model`, which is required for custom models:

```py
custom_util.custom_rename_model(cr, "x_project_task", "custom.project.task")
```

---

### Field renaming

`custom_rename_field(cr, model, old, new)` — rename a field on a model. Sets the field state to
`base` and queues the rename for the post-rename refactor pass (see `do_pending_refactors`):

```py
custom_util.custom_rename_field(cr, "sale.order", "x_studio_delivery_note", "delivery_note")
custom_util.custom_rename_field(cr, "res.partner", "x_vip_client", "is_vip")
```

---

### Module renaming

`custom_rename_module(cr, old, new)` — rename a custom module. Unlike `util.rename_module`, this
handles the case where the new module name was already registered as `uninstalled` by
`update_list()` before the migration ran:

```py
custom_util.custom_rename_module(cr, "my_project_ext", "my_project")
```

---

### Studio / custom field transfer

`transfer_custom_fields(cr, src_module, dest_module, fields_to_transfer)` — move Studio or
custom fields from one module to another, optionally renaming them. The `x_studio_` / `x_`
prefix is stripped automatically when no explicit new name is given:

```py
# 2-tuple (model, field): just move; prefix stripped automatically
# 3-tuple (model, old_field, new_field): move and rename explicitly
custom_util.transfer_custom_fields(cr, "studio_customization", "my_module", [
    ("res.partner", "x_studio_vip"),                      # → "vip"
    ("sale.order", "x_studio_delivery_note"),             # → "delivery_note"
    ("sale.order", "x_custom_ref", "reference_code"),     # explicit rename
])
```

---

### XML ID renaming

`rename_xmlids(cr, pairs, detect_module=True, noupdate=None)` — rename a batch of external
identifiers. When `detect_module=True` (default), short names without a module prefix are
automatically resolved to the calling migration script's module:

```py
custom_util.rename_xmlids(cr, [
    # fully qualified: move across modules
    ("old_module.old_record_id", "new_module.new_record_id"),
    # short name: current module is detected from call stack
    ("old_local_xmlid", "new_local_xmlid"),
])
```

---

### Updating related fields

`update_related_field(cr, list_fields)` — update the `related` attribute on `ir.model.fields`
records when a field has been renamed. Useful after renaming fields that are referenced via
`related=` in other field definitions:

```py
# list of (model, old_field_name, new_field_name) triples
custom_util.update_related_field(cr, [
    ("sale.order.line", "x_mo_id", "manufacturing_order_id"),
    ("sale.order", "x_delivery_note", "delivery_note"),
])
```

---

### Updating views with renamed fields

`update_custom_views(cr, list_fields)` — do a text search-and-replace across all `ir.ui.view`
arch XML for the specified old field names. Use this as a quick sweep when `edit_views` would be
overkill:

```py
custom_util.update_custom_views(cr, [
    ("sale.order.line", "x_mo_id", "manufacturing_order_id"),
    ("sale.order", "x_delivery_note", "delivery_note"),
])
```

---

### Post-rename refactors

After renaming fields with `custom_rename_field`, call `do_pending_refactors` to update all
indirect references in server actions, mail templates, and similar records:

```py
# In a pre- script: rename the fields
custom_util.custom_rename_field(cr, "sale.order", "x_old_field", "new_field")
custom_util.custom_rename_field(cr, "res.partner", "x_vip", "is_vip")

# At the end of the script: flush all queued rename fixes
do_pending_refactors(cr)
```

`fix_renames_in_fields(cr, names_map)` — lower-level version that applies a rename map across
all default models (server actions, mail templates, etc.) without using the pending queue:

```py
fix_renames_in_fields(cr, {"x_old_field": "new_field", "x_vip": "is_vip"})
```

`fix_renames_in_records(cr, names_map, model, ids_or_xmlids=None, fields=None)` — apply a rename
map to a specific model, optionally restricted to certain records or fields:

```py
# Fix only in mail.template, all records
fix_renames_in_records(cr, {"x_old": "new_field"}, "mail.template")

# Fix only specific server actions
fix_renames_in_records(
    cr,
    {"x_amount": "amount_custom"},
    "ir.actions.server",
    ids_or_xmlids=["my_module.action_compute", "my_module.action_confirm"],
)
```

---

### Updating translations

`rename_in_translation(cr, name, values_mapping, res_ids, whole_words=True)` — apply renames
inside translated fields (eg. HTML/Jinja templates stored in `ir.translation`). Useful
when field renames must also be reflected in translated content:

```py
# name is "<model>,<field>" — same format as ir.translation records
rename_in_translation(
    cr,
    name="mail.template,body_html",
    values_mapping={"x_old_field": "new_field"},
    res_ids=[42, 57],  # restrict to specific template ids; pass [] for all
)
```

`build_chained_replace(field_name, values_mapping, whole_words=True)` — generate a chained
PostgreSQL `regexp_replace(...)` expression for bulk SQL updates. Returns a `(sql_expr, params)`
tuple ready to use in a `cr.execute` call:

```py
sub_expr, kwargs = build_chained_replace(
    "body_html",
    {"x_old_amount": "amount", "x_ref": "reference"},
)
cr.execute(
    f"UPDATE mail_template SET body_html = {sub_expr} WHERE id IN %(ids)s",
    {**kwargs, "ids": (1, 2, 3)},
)
```

---

### View editing

`edit_views(cr, view_operations, verbose=True, update_arch=True, create_missing_cows=False, website_id=…)`
is the main entry point for patching views. It accepts a mapping of view identifiers to sequences
of `ViewOperation` instances:

```py
custom_util.edit_views(cr, {
    # by xmlid
    "my_module.view_order_form_custom": [
        RenameElements("x_old_field", "new_field"),
        RemoveFields("x_obsolete_field"),
        AddInvisibleSiblingFields("product_uom_id", "product_uom_category_id"),
    ],
    # by Studio xmlid shorthand (module prefix added automatically)
    "odoo_studio_sale_order_a1b2c3d4": [
        UpdateAttributes('//field[@name="date_order"]', invisible="1"),
        AddElements('//field[@name="partner_id"]', '<field name="ref"/>', position="after"),
    ],
    # by integer id
    1234: [
        RemoveElements('//group[@name="deprecated_group"]'),
    ],
    # by ViewKey (for website views)
    ViewKey("website_sale.product_item", website_id=1): [
        ReplaceValue("old_class", "new_class"),
    ],
})
```

#### Website views

`edit_website_views(cr, view_operations, website_id=WebsiteId.NOTNULL, create_missing=False)`
is a wrapper around `edit_views` that interprets plain strings as view **keys** rather than
xmlids. Use it for COW-ed website views:

```py
custom_util.edit_website_views(cr, {
    "website_sale.product_item": [
        RemoveElements("//div[@class='ribbon ribbon-top-right']"),
    ],
}, website_id=1)
```

Set `create_missing=True` to COW-create the view if it does not yet exist for the website:

```py
custom_util.edit_website_views(cr, {
    "website.footer_custom": [
        AddElementsFromFile(
            "//xpath[contains(@expr, \"@id='footer'\")]",
            osp.join(osp.dirname(__file__), "footer.xml"),
            position="replace",
        ),
    ],
}, website_id=1, create_missing=True)
```

#### Activate / deactivate views

```py
custom_util.activate_views(cr, "my_module.view_to_enable")
custom_util.deactivate_views(cr, ["my_module.view1", "my_module.view2"])
# also accepts integer ids and ViewKey objects
custom_util.activate_views(cr, xmlids=["my_module.view_a", "my_module.view_b"])
```

#### Studio views

`set_studio_view(cr, path, inherit_xml_id)` — create or update a Studio view from an XML file,
or delete it if the file is empty. Must be called from an `end-` script because `web_studio`
must be loaded in the registry:

```py
import os.path as osp

custom_util.set_studio_view(
    cr,
    path=osp.join(osp.dirname(__file__), "studio_customization.xml"),
    inherit_xml_id="sale.view_order_form",
)
```

`reset_studio_view_priority(cr, studio_view_xml_id)` — recalculate the priority of a Studio view
so it remains higher than all other views in the inherited hierarchy. Useful after a new standard
view with a high priority is added:

```py
custom_util.reset_studio_view_priority(cr, "studio_customization.odoo_studio_sale_order_abc123")
```

`create_studio_view(cr, path, model="", inherit_xml_id="", type="form")` — **deprecated** alias
for `set_studio_view`. Prefer `set_studio_view` in new scripts.

---

### View operations

All operation classes are importable from `custom_util` and can be passed to `edit_views`.
Operations are callable and can also be applied directly: `op(arch, cr)` or `op.on(arch, cr)`.

#### `AddElements(xpaths, elements_xml, position=AddElementPosition.INSIDE)`

Insert xml fragments at the elements matched by `xpaths`. `position` can be an
`AddElementPosition` enum value or its name string: `"inside"`, `"after"`, `"before"`,
`"replace"`:

```py
# add a field after an existing one
AddElements('//field[@name="partner_id"]', '<field name="partner_ref"/>', position="after")

# wrap matched elements by replacing them
AddElements(
    '//group[@name="main"]',
    '<notebook><page string="Info"><group name="main"/></page></notebook>',
    position="replace",
)
```

#### `AddElementsFromFile(xpaths, filename, source_xpaths="/*", **kwargs)`

Same as `AddElements` but loads the xml to insert from a file on disk. `source_xpaths` selects
which elements to extract from the file (defaults to all root children):

```py
import os.path as osp

AddElementsFromFile(
    "//xpath[contains(@expr, \"@id='footer'\")]",
    osp.join(osp.dirname(__file__), "footer.xml"),
    position="replace",
)
```

#### `CopyElements(source_xpaths, dest_xpaths, from_view=None, **kwargs)`

Copy elements from another part of the same view, or from a completely different view identified
by xmlid or integer id:

```py
# copy within the same view
CopyElements("//*[@id='source_block']", "//div[@id='target_block']", position="after")

# copy from another view
CopyElements(
    "//div[@id='footer']",
    "//div[@id='footer']",
    from_view="website.default_footer",
    position="replace",
)
```

#### `RemoveElements(xpaths)`

Remove all elements matching the given xpath(s). Note that removal changes the document
structure, so place it after other operations:

```py
RemoveElements('//group[@name="deprecated_section"]')
RemoveElements([f"//xpath[{i}]" for i in (3, 5, 7)])
```

#### `RemoveFields(names)`

Shorthand for removing `<field name="…">` elements:

```py
RemoveFields("x_studio_old_field")
RemoveFields(["x_obsolete_1", "x_obsolete_2", "x_obsolete_3"])
```

#### `AddInvisibleSiblingFields(name, sibling_name, position="after")`

Add an invisible `<field>` next to an existing field. A common pattern when a field's domain or
widget depends on a related field that is not in the view:

```py
# adds <field name="product_uom_category_id" invisible="1"/> after every product_uom_id
AddInvisibleSiblingFields("product_uom_id", "product_uom_category_id")
```

#### `RenameElements(name, new_name, xpath="//*")`

Update the `name` attribute on all matching elements. Also updates `<label for="…">` elements:

```py
RenameElements("x_studio_old_field", "new_field")
# restrict to field elements only
RenameElements("date_invoice", "invoice_date", xpath="//field")
```

#### `UpdateAttributes(xpaths, *dict_args, **dict_kwargs)`

Set or remove attributes on matched elements. Pass `None` as a value to remove an attribute:

```py
# set invisible on a field
UpdateAttributes('//field[@name="legacy_field"]', invisible="1")

# update an xpath expression in a studio view
UpdateAttributes(
    "//xpath[contains(@expr, 'item_ids')][2]",
    {"expr": '//page[@name="config"]/group', "position": "after"},
)

# remove an attribute
UpdateAttributes('//field[@name="asset_id"]', invisible=None)
```

#### `ReplaceValue(pattern, repl, xpaths="//*", position=ReplacePosition.ATTRIBUTES)`

Search-and-replace inside element attributes and/or text. `pattern` can be a plain string or a
compiled `re.Pattern`. `position` accepts a `ReplacePosition` flag or its name string:
`"ATTRIBUTES"`, `"TEXT"`, `"ANY"`:

```py
# rename a field reference in all attributes
ReplaceValue("date_invoice", "invoice_date")

# also replace in element text content
ReplaceValue("date_invoice", "invoice_date", position=ReplacePosition.ANY)

# use a regex pattern
import re
ReplaceValue(re.compile(r"\bx_studio_"), "", position="ATTRIBUTES")

# restrict to specific elements
ReplaceValue("old_action", "new_action", xpaths='//button[@name]')
```

#### `MoveElements(xpaths, destination, prune_parents=True)`

Move matched elements into a destination element. Empty ancestor elements are automatically
pruned after the move unless `prune_parents=False`:

```py
MoveElements(
    "//xpath[contains(@expr, \"class='col-md-12'\")]/div",
    "/data",
)
```

#### `XPathOperation` (base class)

Subclass `XPathOperation` to create reusable custom operations backed by an xpath selector.
Override `__call__(self, arch, cr=None)`:

```py
class FixLegacyWidget(XPathOperation):
    def __init__(self, field_name):
        super().__init__(f'//field[@name="{field_name}"][@widget="legacy_widget"]')

    def __call__(self, arch, cr=None):
        for el in self.get_elements(arch):
            el.attrib["widget"] = "new_widget"

custom_util.edit_views(cr, {
    "my_module.some_view": [FixLegacyWidget("x_field")],
})
```

---

### View identifier helpers

#### `ViewKey(key, website_id=WebsiteId.NOTSET)`

Represents a website view reference by its `key` and `website_id`. Use in `edit_views` when you
need to address a view by key rather than xmlid:

```py

# target the COWed view for website 1
ViewKey("website_sale.product_item", website_id=1)

# target any COWed view regardless of website
ViewKey("website_sale.product_item", website_id=WebsiteId.NOTNULL)

# target the template view (no website)
ViewKey("website_sale.product_item", website_id=None)
```

`WebsiteId` enum values for the `website_id` argument:

| Value | Meaning |
|---|---|
| `WebsiteId.NOTSET` | Match any `website_id` (default) |
| `WebsiteId.NOTNULL` | Match any non-NULL `website_id` |
| `None` | Match views with no `website_id` (template views) |
| `int` | Match a specific website |

#### `get_views_ids(cr, views, …)`

Resolve a mix of xmlids, integer ids, and `ViewKey` objects to a set of view ids:

```py
ids = get_views_ids(cr, "my_module.view_form", 42, ViewKey("website.footer_custom", 1))

# as a mapping from input → matched ids
id_map = get_views_ids(cr, ["my_module.view_a", "my_module.view_b"], mapped=True)
```

#### `get_website_views_ids(cr, keys, website_id=WebsiteId.NOTNULL, create_missing=False)`

Resolve website view keys to their ids:

```py
ids_map = custom_util.get_website_views_ids(
    cr,
    ["website_sale.product_item", "website.footer_custom"],
    website_id=1,
    create_missing=True,
)
# ids_map["website_sale.product_item"] → 1234
```

#### `get_arch(cr, view)`

Fetch and parse the `arch` XML of a view (by xmlid or integer id) as an `lxml.etree` element:

```py
arch = get_arch(cr, "my_module.view_order_form")
arch = get_arch(cr, 1234)
```

#### `extract_elements(arch, xpaths, view_name=None)`

Extract elements from a parsed arch as an XML fragment string. The result can be used directly
as the `elements_xml` argument of `AddElements`:

```py
arch = get_arch(cr, "my_module.source_view")
xml_fragment = extract_elements(arch, "//group[@name='details']")
```

#### `extract_elements_from_view(cr, view, xpaths)`

Convenience wrapper that fetches the arch and extracts elements in one call:

```py
xml_fragment = extract_elements_from_view(cr, "my_module.source_view", "//group[@name='details']")
```

#### `create_cow_views(cr, keys, website_id)` / `create_cow_view(cr, key, website_id)`

COW-create website-specific view copies from their template views. Returns a mapping of
`key → id` (or just the id for the singular form):

```py
ids_by_key = create_cow_views(cr, ["website.footer_custom", "website.header"], website_id=1)
footer_id = create_cow_view(cr, "website.footer_custom", website_id=1)
```

---

### Dashboard utilities

`remove_broken_dashboard_actions(cr, broken_elements_xpaths, views_ids=None)` — remove
invalid saved views or filters from user dashboard views. Useful when JS errors prevent the
user from cleaning them up through the UI:

```py
custom_util.remove_broken_dashboard_actions(cr, [
    "//action[@string='Old Report Name']",
    "//action[@name='action_old_wizard']",
])

# restrict to specific ir.ui.view.custom ids
custom_util.remove_broken_dashboard_actions(cr, ["//action[@name='broken']"], views_ids=[10, 20])
```

`cleanup_old_dashboards(cr)` — delete all but the most recent COW dashboard view per user,
reducing database bloat from accumulated `ir.ui.view.custom` records:

```py
custom_util.cleanup_old_dashboards(cr)
```

---

### Data migration

`merge_model_and_data(cr, source_model, target_model, copy_fields, set_values=None)` — copy all
records from `source_model` into `target_model`, remap all references, then delete the source
model. Columns present in `copy_fields` but missing from the target table are created
automatically:

```py
custom_util.merge_model_and_data(
    cr,
    source_model="x_custom_task",
    target_model="project.task",
    copy_fields=[
        "name",
        "active",
        ("x_description", "description"),  # rename on the way
        ("x_partner_id", "partner_id"),
    ],
    set_values={"task_type": "custom"},
)
```

---

### Updating record relationships

`update_relationships(cr, model, old_id, new_id)` — **deprecated**. Reassign all many2one and
many2many references to `old_id` on the given model to `new_id` instead. Use
`util.replace_record_references` in new scripts:

```py
# deprecated — prefer util.replace_record_references
custom_util.update_relationships(cr, "res.partner", old_id=42, new_id=57)
```

---

### Group merging

`merge_groups(cr, src_xmlid, dest_xmlid)` — merge a `res.groups` record into another, moving all
users and references across. Adds a migration report entry listing affected users:

```py
custom_util.merge_groups(
    cr,
    "my_module.group_old_manager",
    "my_module.group_manager",
)
```

---

### Module utilities

`modules_already_installed(cr, *modules)` — return `True` only when **all** listed modules are
in `installed` or `to upgrade` state:

```py
if custom_util.modules_already_installed(cr, "sale_management", "account"):
    # safe to reference fields from both modules
    ...
```

`set_not_imported_modules(cr, modules)` — mark custom SaaS modules as `imported = False` so the
Odoo CLOC counts them for customer invoicing:

```py
custom_util.set_not_imported_modules(cr, "my_customization")
custom_util.set_not_imported_modules(cr, ["module_a", "module_b"])
```

`get_migscript_module()` — detect the module name of the currently running migration script by
inspecting the call stack. Useful for helpers that need to know their calling module:

```py
module = custom_util.get_migscript_module()
# → "my_custom_module"  (when called from within my_custom_module/migrations/…/pre-migrate.py)
```

---

### Record helpers

`toggle_active(cr, model, ids_or_xmlids, …, active)` — activate or deactivate records on any
model that has an `active` column. Logs a warning when some records are already in the requested
state:

```py
# activate
toggle_active(cr, "product.template", ["my_module.product_archived"], active=True)

# deactivate by integer id
toggle_active(cr, "res.partner", ids=[42, 57], active=False)
```

`get_ids(cr, ids_or_xmlids, …, model)` — resolve a mix of xmlids and integer ids to a set of
record ids for a given model. Can auto-expand short Studio xmlids:

```py
ids = get_ids(cr, "my_module.record_a", 42, model="sale.order")

# as a mapping from input → matched id(s)
id_map = get_ids(cr, ["my_module.record_a", "my_module.record_b"], model="sale.order", mapped=True)
```

`expand_studio_xmlids(xmlids, do_raise=True)` — expand bare Studio view names (those matching
`odoo_studio_…`) into fully qualified `studio_customization.odoo_studio_…` xmlids:

```py
full_xmlids = expand_studio_xmlids([
    "odoo_studio_sale_order_a1b2c3d4",
    "studio_customization.odoo_studio_already_full",
])
# → ["studio_customization.odoo_studio_sale_order_a1b2c3d4",
#    "studio_customization.odoo_studio_already_full"]
```

`STUDIO_XMLID_RE` — compiled regex that matches xmlid **names** (without the module prefix)
generated by Studio. Useful when you need to detect or filter Studio xmlids manually:

```py
if STUDIO_XMLID_RE.match("odoo_studio_sale_order_a1b2c3d4"):
    print("this is a studio xmlid name")
```

`get_existing_models_fields(cr, models_fields)` — filter a `{model: [fields]}` mapping down to
only the models and columns that actually exist in the database. Accepts frozensets of model
names as keys to specify multiple models with the same field list:

```py
existing = get_existing_models_fields(cr, {
    "sale.order": ["x_delivery_note", "x_ref"],
    frozenset(("mail.mass_mailing", "mailing.mailing")): ["body_html", "body_arch"],
})
# → only entries whose table and columns exist
```

`get_model_xmlid_basename(model_name)` — convert a dotted model name into the base name used
for its `ir.model.data` external identifier (the `name` column without the module prefix):

```py
get_model_xmlid_basename("sale.order")    # → "model_sale_order"
get_model_xmlid_basename("res.partner")  # → "model_res_partner"
```

---

### XML utilities

`indent_tree(elem, level=0)` — reindent / pretty-print an `lxml` element tree. Used internally
by `edit_views` after each operation, but also available standalone when you build or modify XML
manually:

```py
from lxml import etree

arch = etree.fromstring("<form><field name='name'/></form>")
indent_tree(arch)
print(etree.tostring(arch, encoding="unicode"))
```

---

### Migration reports

`add_view_modifications_to_migration_reports(cr, view_xmlid_to_operations, announce=True)` — log
view patch operations to the upgrade migration report. Generates a clickable link to each view
with a collapsible list of the operations applied:

```py
add_view_modifications_to_migration_reports(cr, {
    "my_module.view_order_form_custom": [
        RemoveFields("x_obsolete"),
        RenameElements("x_field", "new_field"),
    ],
    "my_module.view_partner_form_custom": [
        UpdateAttributes('//field[@name="ref"]', invisible="1"),
    ],
})
```
