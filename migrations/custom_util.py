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


def custom_rename_module(cr, old, new):
    """
    Renames a custom module from the old name to the new one.

    Using `util.rename_module` for custom modules fails, it's only intended to be
    used in `base` `pre` migration scripts, and it doesn't check for module records
    created by `ir_module.py` `update_list()` that runs before any other migration.
    So if the new module is found in the addons path, the renaming will raise
    a unique name constraint error with those added records.

    This function checks and removes such existing module record, if it's in a
    `uninstalled` or `to install` state, before attempting the rename.
    """
    if not modules_already_installed(cr, new):
        util.remove_module(cr, new)

    # TODO: Maybe do more sanity checks on new/old module current db states

    util.rename_module(cr, old, new)


#FIXME Update all fields where related contains the old field (be sure it's pointing the right model)
def update_related_field(cr, list_fields):
    """ Syntax of list_fields = [('sale.order.line', 'x_mo_id', 'mo_id'),]"""
    related_fields = util.env(cr)['ir.model.fields'].search([('related','!=',False)])
    for field_id in related_fields:
      for model, old, new in list_fields:
        if old in field_id.related:
          cr.execute("""UPDATE ir_model_fields SET related = REPLACE(related, %s, %s) WHERE id = %s""", (old, new, field_id.id))


def update_relationships(cr, model, old_id, new_id):
    """
    Updates relationships to the given model from an old record to the new one.

    N.B. `reference` and `many2one_reference` are not handled.
    """
    cr.execute(
        """
        SELECT name, model, ttype, relation_table, column1, column2
          FROM ir_model_fields
         WHERE relation = %s
           AND ttype IN ('many2one', 'many2many')
           AND store IS TRUE
        """,
        [model],
    )
    related_fields = cr.fetchall()

    if not related_fields:
        return

    _logger.info(
        f'Updating relationships to "{model}" from record id {old_id} to {new_id}'
    )

    for name, model, ttype, relation_table, column1, column2 in related_fields:
        if ttype == "many2one":
            cr.execute(
                """
                UPDATE "{table}"
                   SET "{column}" = %(new_id)s
                 WHERE "{column}" = %(old_id)s
                """.format(
                    table=util.table_of_model(cr, model), column=name
                ),
                dict(old_id=old_id, new_id=new_id),
            )
        elif ttype == "many2many":
            cr.execute(
                """
                INSERT INTO "{table}" ("{column1}", "{column2}")
                     SELECT "{column1}", %(new_id)s
                       FROM "{table}"
                      WHERE "{column2}" = %(old_id)s
                ON CONFLICT DO NOTHING
                """.format(
                    table=relation_table, column1=column1, column2=column2
                ),
                dict(old_id=old_id, new_id=new_id),
            )
            cr.execute(
                """
                DELETE FROM "{table}"
                      WHERE "{column2}" = %(old_id)s
                """.format(
                    table=relation_table, column2=column2
                ),
                dict(old_id=old_id),
            )
        else:
            _logger.error(f'Got unhandled ttype "{ttype}" for field "{model}.{name}"')
            continue


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


def modules_already_installed(cr, *modules):
    """return True if all `modules` are already installed"""
    if not modules:
        raise AttributeError("Must provide at least one module name to check")
    cr.execute(
        """
            SELECT count(1)
              FROM ir_module_module
             WHERE name IN %s
               AND state IN %s
    """,
        [modules, ("installed", "to upgrade")],
    )
    return cr.fetchone()[0] == len(modules)


def merge_groups(cr, src_xmlid, dest_xmlid):
    """
    Merges a `res.groups` into another.

    :param cr: the db cursor
    :param src_xmlid: the `xml id` of the source group (to be merged)
    :param dest_xmlid: the `xml id` of the destination group (to merge into)
    :return: True if merging was successful, None if no merging was performed
        (eg. one of the two groups record and/or xml reference does not exist)
    """
    def group_info(xmlid):
        nonlocal cr
        gid = util.ref(cr, xmlid)
        if gid is None:
            return None
        cr.execute(
            """
            SELECT name
              FROM res_groups
             WHERE id = %s
            """,
            [gid],
        )
        (name,) = cr.fetchone()
        return gid, name

    src_gid, src_name = group_info(src_xmlid)
    dest_gid, dest_name = group_info(dest_xmlid)

    if src_gid is None or dest_gid is None:
        group_info_t = '(id={gid}, name="{name}", xmlid="{xmlid}")'
        if src_gid is None:
            _logger.info(
                "Cannot merge groups, source group not found (already merged?) "
                + group_info_t.format(gid=src_gid, name=src_name, xmlid=src_xmlid)
            )
        elif dest_gid is None:
            _logger.warning(
                "Cannot merge groups, destination group not found "
                + group_info_t.format(gid=dest_gid, name=dest_name, xmlid=dest_xmlid)
            )
        return None

    # Collect users being added to the destination group, for logging purposes
    cr.execute(
        """
        WITH added_uids AS (
            SELECT uid
              FROM res_groups_users_rel
             WHERE gid = %(src_gid)s
            EXCEPT
            SELECT uid
              FROM res_groups_users_rel
             WHERE gid = %(dest_gid)s
        )
        SELECT uid, login
          FROM res_users
          JOIN added_uids
            ON res_users.id = added_uids.uid;
        """,
        dict(src_gid=src_gid, dest_gid=dest_gid),
    )
    added_users = list(cr.fetchall())

    _logger.info(f'Merging group "{src_xmlid}" => "{dest_xmlid}"')

    util.split_group(cr, src_gid, dest_gid)
    update_relationships(cr, "res.groups", src_gid, dest_gid)
    util.remove_record(cr, src_xmlid)

    if added_users:
        added_users_md = "\n".join(
            f" - uid: **{uid}**, login: `{login}`" for uid, login in added_users
        )
        message = (
            f"The group `{src_name}` has been merged into group `{dest_name}`. \n"
            "The following users have been added to the destination group:\n"
            f"{added_users_md}\n"
            "Please make sure these users should *actually* have access to "
            "the additional rights and permissions granted by their new group."
        )
        util.add_to_migration_reports(
            message=message,
            category="Merged Groups",
            format="md",
        )

    return True


def _force_migration_of_fresh_modules(cr, modules):

    for module, path in modules.items():

        if util.module_installed(cr, module):
            _logger.info('Skipping forced migration for module %s, already installed' % module)
            continue

        _logger.info('Forcing migration for module %s' % module)

        util.force_install_module(cr, module)
        util.force_migration_of_fresh_module(cr, module)

        version = path.parts[-2]
        util.import_script(path).migrate(cr, version)

        cr.execute("""
            UPDATE ir_module_module
               SET latest_version = %s
             WHERE name = %s
        """, (version, module,))


def _merge_modules(cr, src_modules, dest_module):

    for module in src_modules:
        _logger.info('Merging %s ⟹ %s' % (module, dest_module))
        util.merge_module(cr, module, dest_module, without_deps=True)


def _uninstall_modules(cr, modules):

    for module in modules:
        _logger.info('Uninstalling module %s' % module)
        util.uninstall_module(cr, module)
        util.remove_module(cr, module)


def _rename_xmlid(cr, values, module):
    """
    """

    noupdate = None

    if isinstance(values, str):

        if '.' not in values:
            _logger.error('Skipping renaming %s, it must be a fully qualified external identifier' % values)

        dest_module = module
        src_module, _, name = values.partition('.')
        new_name = name

    else:

        values = list(values)
        if len(values) < 4:
            values.append({})

        src_module, dest_module, name, kwargs = values

        if not name:
            _logger.error('Skipping renaming for %s, missing name' % values)
            return

        if not src_module:
            src_module = module

        if not dest_module:
            dest_module = module

        noupdate = True
        if 'noupdate' in kwargs:
            noupdate = kwargs['noupdate']

        new_name = name
        if 'new_name' in kwargs:
            new_name = kwargs['new_name']

    if not util.modules_installed(cr, *{dest_module, src_module}):
        _logger.error('Skipping renaming %s ⟹ %s because some of the modules (%s, %s) do not exist' % (name, new_name, src_module, dest_module))
        return

    old_xmlid = '%s.%s' % (src_module, name)
    new_xmlid = '%s.%s' % (dest_module, new_name)

    _logger.debug('Renaming %s ⟹ %s' % (old_xmlid, new_xmlid))

    util.rename_xmlid(
        cr,
        old_xmlid,
        new_xmlid,
        noupdate=noupdate
    )


def _check_models(cr, old, new):
    """
    """

    old_t = util.table_of_model(cr, old)
    if not util.table_exists(cr, old_t):
        return -1

    new_t = util.table_of_model(cr, new)
    if util.table_exists(cr, new_t):
        return 1

    return 0


def _rename_field(cr, model, table, old, new, old_modelname=None, remove=False):
    """
    """

    ok = bool(model and table and old and new)
    assert ok, 'model=%s, table=%s, old=%s, new=%s' % (model, table, old, new)

    if not remove:

        _logger.info('Renaming %s\'s field: %s ⟹ %s' % (model, old, new))

        util.rename_field(cr, model, old, new)
        cr.execute("UPDATE ir_model_fields SET state = 'base' WHERE model = %s AND name = %s", (model, new))

    else:
        _logger.info('Removing %s\'s field: %s' % (old_modelname or model, old))
        util.remove_field(cr, old_modelname or model, old)


def _rename_m2m_relations(cr, data):
    """
    """


    for d in data:

        old, new, *fks = d

        if not util.table_exists(cr, old):
            _logger.debug('Skipping migrating m2m table %s, table does not exist' % old)
            return

        if util.table_exists(cr, new):
            _logger.debug('Skipping migrating m2m table %s, table already exists' % new)
            return

        fk1 = None
        fk2 = None

        if fks:
            fk1, fk2 = fks

        if (
            (fk1 and not isinstance(fk1, tuple) and len(fk1) != 3) or
            (fk2 and not isinstance(fk2, tuple) and len(fk2) != 3)
        ):
            _logger.error('Please use a 3-tuple (<old column name>, <new column name>)')
            return

        _logger.debug('Renaming M2M relation %s ⟹ %s' % (old, new))

        cr.execute(f'ALTER TABLE {old} RENAME TO {new}')

        if fk1:
            cr.execute(f'ALTER TABLE {new} RENAME COLUMN {fk1[0]} TO {fk1[1]}')

        if fk2:
            cr.execute(f'ALTER TABLE {new} RENAME COLUMN {fk2[0]} TO {fk2[1]}')


def _rename_model_fields(cr, model, fields, old_modelname=None):

    table = util.table_of_model(cr, model)

    for field in fields:

        field = list(field)
        if len(field) < 3:
            field.append({})

        new, old, kwargs = field

        _rename_field(
            cr,
            model,
            table,
            old,
            new,
            old_modelname=old_modelname,
            remove=kwargs.get('to_delete', False)
        )


def _upgrade_custom_models(cr, datas, skipped_models=None):

    skipped_models = skipped_models or []

    for data in datas:

        new_modelname, old_modelname, xmlid, fields = data

        if new_modelname in skipped_models:
            _logger.debug('Skipping renaming model %s forced' % old_modelname)
            continue

        check = _check_models(cr, old_modelname, new_modelname)
        if check in (1, -1):

            if check == 1:
                _logger.error('Skipping migrating model %s, table already exists' % new_modelname)
            else:
                _logger.error('Skipping migrating model %s, table for %s does not exist' % (new_modelname, old_modelname))

            continue

        _logger.info('Renaming model: %s ⟹ %s ' % (old_modelname, new_modelname))

        util.rename_model(cr, old_modelname, new_modelname)
        cr.execute("UPDATE ir_model SET state = 'base' WHERE model = %s", (new_modelname,))
        cr.execute(
            "UPDATE ir_model_data SET name = %s WHERE model = 'ir.model' AND name = %s",
            ('model_%s' % new_modelname.replace(".", "_"), xmlid),
        )

        if not fields:
            continue

        _rename_model_fields(cr, new_modelname, fields, old_modelname=old_modelname)


def _upgrade_standard_models(cr, data):

    for model, fields in data.items():

        if not fields:
            continue

        _logger.info('Renaming model %s\'s fields' % model)

        _rename_model_fields(cr, model, fields)
