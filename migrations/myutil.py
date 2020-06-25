from odoo.upgrade import util


def custom_rename_field(cr, model, old, new):
    cr.execute("UPDATE ir_model_fields set state='base' where model=%s and name=%s", (model, old))
    util.rename_field(cr, model, old, new)
