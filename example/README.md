# Migration Example: Cleaning up a Studio-created model

This example walks through a realistic migration scenario: a Studio-built model that has grown
over time, acquired relationships to standard models, and spread its fingerprints across views,
automations, and reports. The migration goal is to move everything into a proper technical
module with clean names.

---

## Starting state

The customer used Odoo Studio to build a lightweight CRM-style tracking model. Studio stored
everything under `studio_customization`. After the project grew, a proper module
`my_messed_up_module` was written to replace it. The migration script must bridge the gap.

---

## Data model

### Custom model — `x_studio_messed_up_model`

Created by Studio. Stored in `studio_customization`. Migration target: `messed.up.model` in
`my_messed_up_module`.

| Studio field name | Type | Description | Migration target name |
|---|---|---|---|
| `x_name` | `Char` | Display name | `name` |
| `x_studio_partner_ids` | `One2many` → `res.partner` | Partners linked to this record (via `res.partner.x_studio_messed_up_id`) | `partner_ids` |
| `x_studio_task_ids` | `Many2many` → `project.task` | Tasks linked to this record | `task_ids` |
| `x_studio_status` | `Selection` | Status field | `status` |
| `x_studio_date` | `Date` | Scheduled date | `date` |

**Menu, action, and views created by Studio:**

| Record type | Studio xmlid | Migration target xmlid |
|---|---|---|
| Menu item | `studio_customization.studio_menu_messed_up` | `my_messed_up_module.menu_messed_up` |
| Window action | `studio_customization.studio_action_messed_up` | `my_messed_up_module.action_messed_up` |
| Form view | `studio_customization.odoo_studio_messed_up_view_form` | `my_messed_up_module.view_messed_up_form` |
| List view | `studio_customization.odoo_studio_messed_up_view_list` | `my_messed_up_module.view_messed_up_list` |
| Search view | `studio_customization.odoo_studio_messed_up_view_search` | `my_messed_up_module.view_messed_up_search` |

---

### Fields added to `res.partner` by Studio

| Studio field name | Type | Description | Migration target |
|---|---|---|---|
| `x_studio_messed_up_id` | `Many2one` → `x_studio_messed_up_model` | Back-reference for the O2M | `messed_up_id` in `my_messed_up_module` |

Studio also patched the `res.partner` form view to show this field and a related O2M inline
list. The Studio inheritance view is:

```
studio_customization.odoo_studio_res_partner_form_<hash>
  └── inherits: base.view_partner_form
      └── adds: x_studio_messed_up_id (Many2one)
                x_studio_partner_ids (inline O2M list of linked messed_up records)
```

---

### Fields added to `project.task` by Studio

| Studio field name | Type | Description | Migration target |
|---|---|---|---|
| `x_studio_messed_up_id` | `Many2one` → `x_studio_messed_up_model` | Link from task to the tracking record | `messed_up_id` in `my_messed_up_module` |

Studio patched two views on `project.task`:

```
studio_customization.odoo_studio_project_task_view_search_<hash>
  └── inherits: project.view_task_search_form
      └── adds: filter on x_studio_messed_up_id

studio_customization.odoo_studio_project_task_view_list_<hash>
  └── inherits: project.view_task_all_tree
      └── adds: x_studio_messed_up_id column
```

---

### Automations (`base.automation`)

Two automated actions were created through Studio's automation editor. Both reference the
Studio field names as plain text (in domain strings and Python code), so they will break after
the field renames unless explicitly fixed.

#### Automation 1 — filter by M2O field

Triggers on `project.task` records. Uses `x_studio_messed_up_id` in the pre-filter domain:

```
xmlid: studio_customization.automation_task_on_messed_up
model: project.task
trigger: on_write
domain_pre_filter: [("x_studio_messed_up_id", "!=", False)]
```

After migration the domain must reference `messed_up_id` instead.

#### Automation 2 — iterate M2M in code

Triggers on `x_studio_messed_up_model`. Uses `x_studio_task_ids` in the server action code:

```
xmlid: studio_customization.automation_messed_up_sync_tasks
model: x_studio_messed_up_model  →  messed.up.model
trigger: on_write
code:
    for task in record.x_studio_task_ids:
        task.write({"x_studio_messed_up_id": record.id})
```

After migration the code must reference `task_ids` and `messed_up_id`.

`do_pending_refactors` handles these automatically since both field names appear as plain text in
stored records. Verify the result manually after the migration.

---

### Sales order quotation report

Studio patched the standard `sale.report_saleorder` QWeb report to add a table of linked
partners (the O2M `x_studio_partner_ids`) inline in the quotation body:

```
studio_customization.odoo_studio_sale_report_<hash>
  └── inherits: sale.report_saleorder
      └── adds: table iterating o.partner_id.x_studio_partner_ids
                showing x_name, x_studio_status, x_studio_date
```

The arch references both the inverse field (`x_studio_partner_ids`) and the fields of
`x_studio_messed_up_model` by their Studio names. After migration these must be updated to
`partner_ids`, `name`, `status`, and `date`. Because this is a report view (not a form/list),
`do_pending_refactors` will not fix it — an explicit `edit_views` call is required.

---

### Website COW view

The customer customised the `/shop` product page on website 1. Odoo created a COW copy of the
template view:

```
Template view (no website_id):  website_sale.product_item
COW copy (website_id = 1):      website_sale.product_item  (separate record)
```

The COW view was edited in the website editor to add a badge showing `x_studio_status` pulled
from a related `messed.up.model` record. After the field rename the badge template will still
contain the old field name and must be patched via `edit_website_views`.

---

## Migration challenges and helper mapping

| What needs fixing | Why it is tricky | Helper(s) to use |
|---|---|---|
| Rename model `x_studio_messed_up_model` → `messed.up.model` | Studio sets model state to `manual`; standard `util.rename_model` rejects it | `custom_rename_model` |
| Rename all `x_studio_*` fields | Same state issue; renames must be queued for the refactor pass | `custom_rename_field` |
| Move fields from `studio_customization` to `my_messed_up_module` | Fields are owned by `studio_customization`; ownership must be transferred before renaming | `transfer_custom_fields` |
| Rename xmlids (menu, action, views) | xmlids still point to `studio_customization`; must be remapped | `rename_xmlids` |
| Fix automation domain `[("x_studio_messed_up_id", !=, False)]` | Domain stored as a plain text string in `base.automation.domain_pre_filter` | `do_pending_refactors` |
| Fix automation code `record.x_studio_task_ids` | Python code stored as text in `ir.actions.server.code` | `do_pending_refactors` |
| Patch Studio inheritance views on `res.partner` and `project.task` | Views reference old field names in `name=` attributes | `edit_views` + `RenameElements` / `RemoveFields` |
| Patch the quotation report | Report arch references both old field names and the old O2M path | `edit_views` + `RenameElements` + `ReplaceValue` |
| Patch the COW website view | Must address the specific COW copy (website_id = 1), not the template | `edit_website_views` |
| Log all view changes for consultant review | Consultant must verify report and website changes manually | `add_view_modifications_to_migration_reports` |

---

## Resulting migration scripts

The full scripts live alongside this README:

- [`pre-migrate.py`](pre-migrate.py)
- [`post-migrate.py`](post-migrate.py)
