from odoo.upgrade import custom_util


def migrate(cr, version):
    # 1. Transfer Studio fields out of studio_customization before renaming.
    #    2-tuples strip the x_studio_ prefix automatically.
    #    3-tuples provide an explicit new name.
    custom_util.transfer_custom_fields(
        cr,
        "studio_customization",
        "my_messed_up_module",
        [
            # Fields on the custom model itself
            ("x_studio_messed_up_model", "x_name", "name"),
            ("x_studio_messed_up_model", "x_studio_partner_ids", "partner_ids"),
            ("x_studio_messed_up_model", "x_studio_task_ids", "task_ids"),
            ("x_studio_messed_up_model", "x_studio_status", "status"),
            ("x_studio_messed_up_model", "x_studio_date", "date"),
            # Fields added to standard models
            ("res.partner", "x_studio_messed_up_id", "messed_up_id"),
            ("project.task", "x_studio_messed_up_id", "messed_up_id"),
        ],
    )

    # 2. Rename the model itself (must come after field transfer so records exist).
    custom_util.custom_rename_model(cr, "x_studio_messed_up_model", "messed.up.model")

    # 3. Rename xmlids (menu, action, views).
    custom_util.rename_xmlids(
        cr,
        [
            ("studio_customization.studio_menu_messed_up", "my_messed_up_module.menu_messed_up"),
            ("studio_customization.studio_action_messed_up", "my_messed_up_module.action_messed_up"),
            ("studio_customization.odoo_studio_messed_up_view_form", "my_messed_up_module.view_messed_up_form"),
            ("studio_customization.odoo_studio_messed_up_view_list", "my_messed_up_module.view_messed_up_list"),
            ("studio_customization.odoo_studio_messed_up_view_search", "my_messed_up_module.view_messed_up_search"),
        ],
    )
