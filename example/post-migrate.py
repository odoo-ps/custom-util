from odoo.upgrade.custom_util import (
    RenameElements,
    ReplaceValue,
    add_view_modifications_to_migration_reports,
    do_pending_refactors,
    edit_views,
    edit_website_views,
)


def migrate(cr):
    # 1. Flush all queued field renames into server actions, mail templates,
    #    automation domains/code, and any other records that store field names as text.
    do_pending_refactors(cr)

    # 2. Patch Studio inheritance views on res.partner.
    #    RenameElements updates name= attributes; RemoveFields drops anything
    #    that was removed from the model entirely.
    partner_view_ops = {
        "studio_customization.odoo_studio_res_partner_form_abcd1234": [
            RenameElements("x_studio_messed_up_id", "messed_up_id"),
            RenameElements("x_studio_partner_ids", "partner_ids"),
        ],
    }

    # 3. Patch Studio inheritance views on project.task.
    task_view_ops = {
        "studio_customization.odoo_studio_project_task_view_search_abcd1234": [
            RenameElements("x_studio_messed_up_id", "messed_up_id"),
        ],
        "studio_customization.odoo_studio_project_task_view_list_abcd1234": [
            RenameElements("x_studio_messed_up_id", "messed_up_id"),
        ],
    }

    # 4. Patch the quotation report.
    #    ReplaceValue handles attribute values that embed the old field name
    #    as part of a longer expression (e.g. `o.partner_id.x_studio_partner_ids`).
    report_ops = {
        "studio_customization.odoo_studio_sale_report_abcd1234": [
            RenameElements("x_studio_partner_ids", "partner_ids"),
            RenameElements("x_name", "name"),
            RenameElements("x_studio_status", "status"),
            RenameElements("x_studio_date", "date"),
            ReplaceValue("x_studio_partner_ids", "partner_ids"),
            ReplaceValue("x_studio_status", "status"),
        ],
    }

    all_ops = {**partner_view_ops, **task_view_ops, **report_ops}
    edit_views(cr, all_ops)

    # 5. Patch the COW website view (addressed by key + website_id, not xmlid).
    website_ops = {
        "website_sale.product_item": [
            ReplaceValue("x_studio_status", "status"),
        ],
    }
    edit_website_views(cr, website_ops, website_id=1)

    # 6. Log all patched views to the migration report for consultant review.
    add_view_modifications_to_migration_reports(cr, {**all_ops, **website_ops})
