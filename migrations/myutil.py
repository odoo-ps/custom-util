# -*- coding: utf-8 -*-

import logging

from odoo.upgrade import util


_logger = logging.getLogger(__name__)


def custom_rename_model(cr, old, new):
    cr.execute("UPDATE ir_model SET state='base' WHERE model=%s", (old))
    util.rename_model(cr, old, new)
    _logger.info('rename model : %s -> %s' % (old, new))


def custom_rename_field(cr, model, old, new):
    cr.execute("UPDATE ir_model_fields SET state='base' WHERE model=%s AND name=%s", (model, old))
    util.rename_field(cr, model, old, new)
    _logger.info('rename field : %s -> %s on model %s'  % (old, new, model))


#FIXME Update all fields where related contains the old field (be sure it's pointing the right model)
def update_related_field(cr, list_fields):
    """ Syntax of list_fields = [('sale.order.line', 'x_mo_id', 'mo_id'),]"""
    related_fields = util.env(cr)['ir.model.fields'].search([('related','!=',False)])
    for field_id in related_fields:
      for model, old, new in list_fields:
        if old in field_id.related:
          cr.execute("""UPDATE ir_model_fields SET related = REPLACE(related, %s, %s) WHERE id = %s""", (old, new, field_id.id))


# Update all views that contains old fields
def update_custom_views(cr, list_fields):
    """ Syntax of list_fields = [('sale.order.line', 'x_mo_id', 'mo_id'),]"""
    views_to_update = util.env(cr)['ir.ui.view'].search([])
    for view_id in views_to_update:
        view_id_tmp = view_id.arch
        view_has_changed = False
        for model, old, new in list_fields:
          if (view_id.model == model or \
             (view_id.model == 'sale.order' and model == 'sale.order.line') or \
             not view_id.model) and old in view_id.arch:
            view_id_tmp = view_id_tmp.replace(old, new)
            view_has_changed = True
        if view_has_changed:
          view_id.arch = view_id_tmp
