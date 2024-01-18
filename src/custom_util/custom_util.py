# -*- coding: utf-8 -*-

import inspect
import logging
import os
import pathlib
import re
import warnings

from odoo.upgrade import util

from . import refactor
from .helpers import get_model_xmlid_basename


# TODO: some of these functions should probably be renamed.
# export also some private _members
__all__ = [
    "custom_rename_model",
    "custom_rename_field",
    "transfer_custom_fields",
    "custom_rename_module",
    "update_related_field",
    "update_relationships",
    "update_custom_views",
    "modules_already_installed",
    "merge_groups",
    "merge_model_and_data",
    "_force_migration_of_fresh_modules",
    "_merge_modules",
    "_uninstall_modules",
    "_rename_xmlid",
    "rename_xmlids",
    "_check_models",
    "_rename_field",
    "_rename_m2m_relations",
    "_rename_model_fields",
    "_upgrade_custom_models",
    "_upgrade_standard_models",
    "migrate_invoice_move_data",
    "get_migscript_module",
    "set_not_imported_modules",
]


_logger = logging.getLogger(__name__)


# --- Patching `util._get_base_version()`


def _patched_get_base_version(cr):
    """
    Patched version of `util._get_base_version()` that works outside of `base` module,
    by setting `__base_version` in `util.ENVIRON` from `base` version in the database.
    """
    if not util.ENVIRON.get("__base_version") and not os.getenv("ODOO_BASE_VERSION"):
        cr.execute("SELECT latest_version FROM ir_module_module WHERE name='base'")
        util.ENVIRON["__base_version"] = util.parse_version(cr.fetchone()[0])

    return _original_get_base_version(cr)


try:
    # For new refactored `util` package
    _original_get_base_version = util.inherit._get_base_version
    util.inherit._get_base_version = _patched_get_base_version
except AttributeError:
    # For old `util.py` code structure
    _original_get_base_version = util._get_base_version
    util._get_base_version = _patched_get_base_version

# --- Done patching


def custom_rename_model(cr, old, new):
    cr.execute("UPDATE ir_model SET state='base' WHERE model=%s", (old,))
    util.rename_model(cr, old, new)
    _logger.info(f"rename model : {old} -> {new}")


def rename_field(cr, model, old, new, set_state_base=True, add_to_refactor=True):
    if set_state_base:
        state_value = "base" if isinstance(set_state_base, bool) else set_state_base
        cr.execute(
            "UPDATE ir_model_fields SET state = %s WHERE model = %s AND name = %s",
            (state_value, model, old),
        )
    util.rename_field(cr, model, old, new)
    if add_to_refactor:
        refactor.FIELD_RENAMES_PENDING[model][old] = new


def custom_rename_field(cr, model, old, new):
    rename_field(cr, model, old, new, set_state_base=True)
    _logger.info(f"rename field : {old} -> {new} on model {model}")


def transfer_custom_fields(cr, src_module, dest_module, fields_to_transfer):
    """
    Move fields from the one module to the other, optionally renaming them.

    :param cr: database cursor object
    :param src_module: the name of the source module for the fields
    :param dest_module: the name of the destination module for the fields
    :param fields_to_transfer: an iterable of 2- or 3-tuples with the following spec:
        2-tuples: (model_name, field_name)  # just moves the fields
        3-tuples: (model_name, existing_field_name, new_field_name)  # also renames the fields
    :raise ValueError: if one of the values of ``fields_to_transfer`` is not a valid tuple
    """
    _logger.info(f'Transferring custom/studio fields to "{dest_module}"')
    for field_spec in fields_to_transfer:
        field_new_name = None
        if len(field_spec) == 2:
            model, field_name = field_spec
        elif len(field_spec) == 3:
            model, field_name, field_new_name = field_spec
        else:
            raise ValueError(f"Field rename must be a 2- or 3-tuple, got: {field_spec}")
        util.move_field_to_module(cr, model, field_name, src_module, dest_module)
        if field_new_name is None:
            field_new_name = re.sub(r"^x_(?:studio_)?", "", field_name)
        if field_new_name and field_new_name != field_name:
            custom_rename_field(cr, model, field_name, field_new_name)


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


# FIXME Update all fields where related contains the old field (be sure it's pointing the right model)
def update_related_field(cr, list_fields):
    """Syntax of list_fields = [('sale.order.line', 'x_mo_id', 'mo_id'),]"""
    related_fields = util.env(cr)["ir.model.fields"].search([("related", "!=", False)])
    for field_id in related_fields:
        for _model, old, new in list_fields:
            if old in field_id.related:
                cr.execute(
                    """UPDATE ir_model_fields SET related = REPLACE(related, %s, %s) WHERE id = %s""",
                    (old, new, field_id.id),
                )


def update_relationships(cr, model, old_id, new_id):
    """
    Updates relationships to the given model from an old record to the new one.

    N.B. `reference` and `many2one_reference` are not handled.
    """
    # TODO: kept for backwards-compatibility, just remove?
    warnings.warn("Use `util.replace_record_references` instead", DeprecationWarning, stacklevel=1)
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

    _logger.info(f'Updating relationships to "{model}" from record id {old_id} to {new_id}')

    for name, rel_model, ttype, relation_table, column1, column2 in related_fields:
        if ttype == "many2one":
            cr.execute(
                """
                UPDATE "{table}"
                   SET "{column}" = %(new_id)s
                 WHERE "{column}" = %(old_id)s
                """.format(table=util.table_of_model(cr, rel_model), column=name),
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
                """.format(table=relation_table, column1=column1, column2=column2),
                dict(old_id=old_id, new_id=new_id),
            )
            cr.execute(
                """
                DELETE FROM "{table}"
                      WHERE "{column2}" = %(old_id)s
                """.format(table=relation_table, column2=column2),
                dict(old_id=old_id),
            )
        else:
            _logger.error(f'Got unhandled ttype "{ttype}" for field "{rel_model}.{name}"')
            continue


# Update all views that contains old fields
def update_custom_views(cr, list_fields):
    """Syntax of list_fields = [('sale.order.line', 'x_mo_id', 'mo_id'),]"""
    views_to_update = util.env(cr)["ir.ui.view"].search([])
    for view_id in views_to_update:
        view_id_tmp = view_id.arch
        view_has_changed = False
        for model, old, new in list_fields:
            if (
                view_id.model == model
                or (view_id.model == "sale.order" and model == "sale.order.line")
                or not view_id.model
            ) and old in view_id.arch:
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
            msg = "Cannot merge groups, source group not found (already merged?) " + group_info_t.format(
                gid=src_gid, name=src_name, xmlid=src_xmlid
            )
            _logger.info(msg)
        elif dest_gid is None:
            msg = "Cannot merge groups, destination group not found " + group_info_t.format(
                gid=dest_gid, name=dest_name, xmlid=dest_xmlid
            )
            _logger.warning(msg)
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
        added_users_md = "\n".join(f" - uid: **{uid}**, login: `{login}`" for uid, login in added_users)
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
            _logger.info(f"Skipping forced migration for module {module}, already installed")
            continue

        _logger.info(f"Forcing migration for module {module}")

        util.force_install_module(cr, module)
        util.force_migration_of_fresh_module(cr, module)

        version = path.parts[-2]
        util.import_script(path).migrate(cr, version)

        cr.execute(
            """
            UPDATE ir_module_module
               SET latest_version = %s
             WHERE name = %s
            """,
            (version, module),
        )


def merge_model_and_data(cr, source_model, target_model, copy_fields, set_values=None):
    """
    Merges a model into another, copying the records, mapping fields, setting missing values.
    Columns present in ``copy_fields`` but missing in the target model's table will
    be automatically created, using the source table's column type.
    IDs on the old model and their references will be remapped to the new ones in the
    target model's table.

    :param cr: the database cursor.
    :param source_model: the model that will be merged (and then deleted).
    :param target_model: the model into where to merge the source one (and its records).
    :param copy_fields: an iterable of field names, or 2-tuples (source_field, target_field),
        or a mix of the two, of the fields/columns that will be copied from the
        source model's table to the target one.
    :param set_values: a mapping of field names (on the target model) to values that
        will be set on the new records copied from the source model's table.
    """
    set_values = set_values or {}

    source_table = util.table_of_model(cr, source_model)
    target_table = util.table_of_model(cr, target_model)

    query_cols_map = {}
    for field_spec in copy_fields:
        if isinstance(field_spec, str):
            field_src = field_dest = field_spec
        elif isinstance(field_spec, (list, tuple)):
            field_src, field_dest = tuple(field_spec)
        else:
            raise TypeError("Invalid value for field name.")

        if not util.column_exists(cr, target_table, field_dest):
            column_type = util.column_type(cr, source_table, field_src)
            util.create_column(cr, target_table, field_dest, column_type)

        query_cols_map[field_dest] = field_src

    query_cols_map.update(dict.fromkeys(set_values.keys(), "%s"))

    old_id_column = f"_{source_table}_id"
    util.create_column(cr, target_table, old_id_column, "int4")

    insert_names = ", ".join(query_cols_map.keys())
    select_names = ", ".join(query_cols_map.values())
    cr.execute(
        f"""
        INSERT INTO {target_table} ({old_id_column}, {insert_names})
             SELECT id, {select_names}
               FROM {source_table}
          RETURNING {old_id_column}, id
        """,
        list(set_values.values()),
    )
    id_map = dict(cr.fetchall())
    if id_map:
        util.replace_record_references_batch(cr, id_map, source_model, target_model)

    util.remove_column(cr, target_table, old_id_column)

    util.merge_model(cr, source_model, target_model)


def _merge_modules(cr, src_modules, dest_module):
    for module in src_modules:
        _logger.info(f"Merging {module} ⟹ {dest_module}")
        util.merge_module(cr, module, dest_module, update_dependers=False)


def _uninstall_modules(cr, modules):
    for module in modules:
        _logger.info(f"Uninstalling module {module}")
        util.uninstall_module(cr, module)
        util.remove_module(cr, module)


# TODO: kept for backwards compatibility, maybe just remove?
def _rename_xmlid(cr, values_or_xmlid, module):
    noupdate = None

    if isinstance(values_or_xmlid, str):
        if "." not in values_or_xmlid:
            _logger.error(f"Skipping renaming {values_or_xmlid}, it must be a fully qualified external identifier")
            return

        dest_module = module
        src_module, name = values_or_xmlid.split(".")
        new_name = name
    else:
        # append empty kwargs
        values = list(values_or_xmlid)
        if len(values) < 3:
            _logger.error(
                f"Skipping renaming for {values_or_xmlid}, please provide either fully "
                "qualified external identifier or "
                "(src_module or None, dest_module or None, name, (kwargs)) as 2nd parameter"
            )
            return
        elif len(values) < 4:
            values.append({})

        src_module, dest_module, name, kwargs = values

        if not name:
            _logger.error(f"Skipping renaming for {values_or_xmlid}, missing name")
            return

        if not src_module:
            src_module = module

        if not dest_module:
            dest_module = module

        noupdate = True
        if "noupdate" in kwargs:
            noupdate = kwargs["noupdate"]

        new_name = name
        if "new_name" in kwargs:
            new_name = kwargs["new_name"]

    modules_to_check = (dest_module, src_module) if dest_module != src_module else (src_module,)
    if not util.modules_installed(cr, *modules_to_check):
        _logger.error(
            f"Skipping renaming {name} => {new_name} because some of the modules "
            f"({src_module}, {dest_module}) are not installed or do not exist"
        )
        return

    old_xmlid = "%s.%s" % (src_module, name)
    new_xmlid = "%s.%s" % (dest_module, new_name)

    _logger.debug(f"Renaming {old_xmlid} ⟹ {new_xmlid}")

    util.rename_xmlid(cr, old_xmlid, new_xmlid, noupdate=noupdate)


def rename_xmlids(cr, pairs, detect_module=True, noupdate=None):
    """Rename a batch of xmlids"""

    def process_xmlid(xmlid):
        nonlocal default_module
        if isinstance(xmlid, (list, tuple)) and len(xmlid) == 2:
            return tuple(xmlid)
        elif isinstance(xmlid, str):
            if "." not in xmlid:
                if default_module:
                    return default_module, xmlid
            else:
                return xmlid.split(".")
        raise ValueError(f"Bad xmlid: {xmlid}")

    default_module = get_migscript_module() if detect_module else None

    for old_xmlid_raw, new_xmlid_raw in pairs:
        try:
            old_module, old_name = process_xmlid(old_xmlid_raw)
            new_module, new_name = process_xmlid(new_xmlid_raw)
        except ValueError as exc:
            _logger.error(f"Skipping xmlid rename {old_xmlid_raw} => {new_xmlid_raw}: {exc}")  # noqa: TRY400
            continue

        old_xmlid = f"{old_module}.{old_name}"
        new_xmlid = f"{new_module}.{new_name}"

        _logger.debug(f"Renaming xmlid {old_xmlid} => {new_xmlid}")
        util.rename_xmlid(cr, old_xmlid, new_xmlid, noupdate=noupdate)


def _check_models(cr, old, new):
    old_t = util.table_of_model(cr, old)
    if not util.table_exists(cr, old_t):
        return -1

    new_t = util.table_of_model(cr, new)
    if util.table_exists(cr, new_t):
        return 1

    return 0


def _rename_field(cr, model, table, old, new, old_modelname=None, remove=False):
    ok = bool(model and table and old and new)
    assert ok, "model=%s, table=%s, old=%s, new=%s" % (model, table, old, new)

    if not remove:
        _logger.info(f"Renaming {model}'s field: {old} ⟹ {new}")
        rename_field(cr, model, old, new, set_state_base=True)
    else:
        _logger.info(f"Removing {old_modelname or model}'s field: {old}")
        util.remove_field(cr, old_modelname or model, old)


def _rename_m2m_relations(cr, data):
    for old, new, *fks in data:
        if not util.table_exists(cr, old):
            _logger.debug(f"Skipping migrating m2m table {old}, table does not exist")
            return

        if util.table_exists(cr, new):
            _logger.debug(f"Skipping migrating m2m table {new}, table already exists")
            return

        fk1 = None
        fk2 = None

        if fks:
            fk1, fk2 = fks

        if (fk1 and not isinstance(fk1, tuple) and len(fk1) != 3) or (
            fk2 and not isinstance(fk2, tuple) and len(fk2) != 3
        ):
            _logger.error("Please use a 3-tuple (<old column name>, <new column name>)")
            return

        _logger.debug(f"Renaming M2M relation {old} ⟹ {new}")

        cr.execute(f"ALTER TABLE {old} RENAME TO {new}")

        if fk1:
            cr.execute(f"ALTER TABLE {new} RENAME COLUMN {fk1[0]} TO {fk1[1]}")

        if fk2:
            cr.execute(f"ALTER TABLE {new} RENAME COLUMN {fk2[0]} TO {fk2[1]}")


def _rename_model_fields(cr, model, fields, old_modelname=None):
    table = util.table_of_model(cr, model)
    for field in fields:
        field = list(field)  # noqa: PLW2901
        if len(field) < 3:
            field.append({})

        new, old, kwargs = field
        _rename_field(cr, model, table, old, new, old_modelname=old_modelname, remove=kwargs.get("to_delete", False))


def _upgrade_custom_models(cr, datas, skipped_models=None):
    skipped_models = skipped_models or []

    for new_modelname, old_modelname, xmlid, fields in datas:
        if new_modelname in skipped_models:
            _logger.debug(f"Skipping renaming model {old_modelname} forced")
            continue

        check = _check_models(cr, old_modelname, new_modelname)
        if check in {1, -1}:
            if check == 1:
                _logger.error(f"Skipping migrating model {new_modelname}, table already exists")
            else:
                _logger.error(f"Skipping migrating model {new_modelname}, table for {old_modelname} does not exist")

            continue

        _logger.info(f"Renaming model: {old_modelname} ⟹ {new_modelname} ")

        util.rename_model(cr, old_modelname, new_modelname)
        cr.execute("UPDATE ir_model SET state = 'base' WHERE model = %s", (new_modelname,))
        cr.execute(
            "UPDATE ir_model_data SET name = %s WHERE model = 'ir.model' AND name = %s",
            (get_model_xmlid_basename(new_modelname), xmlid),
        )

        if not fields:
            continue

        _rename_model_fields(cr, new_modelname, fields, old_modelname=old_modelname)


def _upgrade_standard_models(cr, data):
    for model, fields in data.items():
        if not fields:
            continue

        _logger.info(f"Renaming model {model}'s fields {fields}")

        _rename_model_fields(cr, model, fields)


def migrate_invoice_move_data(cr, fields=None, lines_fields=None, overwrite=False):
    """
    Migrate ``account.invoice`` fields data over to ``account.move``,
    and/or ``account.invoice.line`` fields data over to ``account.move.line``,
    Useful to fix lost data due to accountpocalypse (Odoo 12->13).
    All columns/fields must already exist, so consider using this in a ``post-`` script.

    :param cr: the database cursor object
    :param fields: an iterable of field names `str` or 2-tuples, where the first element
        of the tuple is the name of the source field in ``account.invoice`` and the
        second element is the name of the destination field in ``account.move``.
    :param lines_fields: an iterable of field names `str` or 2-tuples, where the first element
        of the tuple is the name of the source field in ``account.invoice.line`` and the
        second element is the name of the destination field in ``account.move.line``.
    :param overwrite: if set to `True` will overwrites existing values in the destination
        record (move) with the ones from the source one (invoice), otherwise will preserve
        any data that already exists in the destination, writing only missing values.
        Defaults to `False`.
    :raise AttributeError: if none of ``fields`` and ``lines_fields`` are passed.
    """
    if not fields and not lines_fields:
        raise AttributeError('Must provide one or both of "fields", "lines_fields"')
    if not fields:
        fields = []
    if not lines_fields:
        lines_fields = []

    def prepare_statements(field_specs, move_alias, invoice_alias):
        set_stmts = []
        where_not_null = []
        for field_spec in field_specs:
            if isinstance(field_spec, str):
                invoice_field = move_field = field_spec
            elif isinstance(field_spec, (tuple, list)) and len(field_spec) == 2:
                invoice_field, move_field = field_spec
            else:
                raise ValueError(f"Field must be a string or a 2-tuple, got: {field_spec}")

            set_stmts.append(
                f"{move_field} = "
                + (
                    f"{invoice_alias}.{invoice_field}"
                    if overwrite
                    else f"COALESCE({move_alias}.{move_field}, {invoice_alias}.{invoice_field})"
                )
            )
            where_not_null.append(f"{invoice_alias}.{invoice_field} IS NOT NULL")

        return set_stmts, where_not_null

    if fields:
        set_stmts, where_not_null = prepare_statements(fields, "am", "ai")
        cr.execute(f"""
            UPDATE account_move am
               SET {", ".join(set_stmts)}
              FROM account_invoice ai
             WHERE ai.move_id = am.id
               AND ({" OR ".join(where_not_null)})
        """)
        _logger.debug(f'Updated {cr.rowcount} "account.move" records')

    if lines_fields:
        set_stmts, where_not_null = prepare_statements(lines_fields, "aml", "ail")
        cr.execute(f"""
            UPDATE account_move_line aml
               SET {", ".join(set_stmts)}
              FROM account_invoice_line ail
              JOIN invl_aml_mapping map ON map.invl_id=ail.id
             WHERE aml.id = map.aml_id
               AND ({" OR ".join(where_not_null)})
        """)
        _logger.debug(f'Updated {cr.rowcount} "account.move.line" records')


def get_migscript_module():
    """
    When function is called from within a migscript (which it should)
    returns the module of the migscript
    """
    for frame in inspect.stack():
        if frame.function == "migrate":
            path = pathlib.PurePath(frame.filename)
            return path.parts[-4]
    raise RuntimeError("Could not automatically determine calling migration script module name")


def set_not_imported_modules(cr, modules):
    """
    Set custom SAAS modules as not imported for the Odoo CLOC to take them into account
    for invoicing the customer for their developments.

    Examples::
        set_not_imported_modules(cr, "custom_module")
        set_not_imported_modules(cr, list_of_module_names)
        set_not_imported_modules(cr, ["custom_module_1", "custom_module_2])

    :param modules: a name or list of module names to update
    :raise AttributeError: if no module name (``modules``) is passed
    """
    if not modules:
        raise AttributeError("Must provide at least one module name to update")
    if isinstance(modules, str):
        modules = [modules]
    cr.execute(
        """
        UPDATE ir_module_module
           SET imported = FALSE
         WHERE name IN %s
        """,
        [tuple(modules)],
    )
