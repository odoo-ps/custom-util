"""
Utility functions that perform refactor-related migration operations.
These are usually operations that require coordination across multiple models,
records, and changes, and need to be executed in a specific order.
"""
import logging
from collections import defaultdict
from typing import MutableMapping

from odoo.upgrade import util


__all__ = [
    "build_chained_replace",
    "fix_renames_in_fields",
    "rename_in_translation",
    "do_pending_refactors",
]


_logger = logging.getLogger(__name__)


FIELD_RENAMES_PENDING: MutableMapping[str, MutableMapping[str, str]] = defaultdict(dict)
"""A mapping of mappings of done fields renames per model, awaiting refactor operations"""


def build_chained_replace(field_name, values_mapping):
    """
    Utility function that generates PostgreSQL query statements to replace multiple
    values for a column/field, chaining many ``regexp_replace(...)`` together.

    :param field_name: the name of the field/column, used in the first ``regexp_replace``.
    :param values_mapping: a mapping of values from old to new to replace.
    :return: a 2-tuple of the prepared SQL expression and a mapping of placeholder
        names to values to pass to the database driver for literal values substitution.
    """
    sub_expr = field_name
    query_kwargs = {}
    for i, (old_value, new_value) in enumerate(values_mapping.items()):
        old_placeholder = f"old{i}"
        new_placeholder = f"new{i}"
        sub_expr = f"regexp_replace({sub_expr}, %({old_placeholder})s, %({new_placeholder})s, 'g')"
        query_kwargs[old_placeholder] = fr"\m{old_value}\M"
        query_kwargs[new_placeholder] = new_value
    return sub_expr, query_kwargs


# TODO: right now the implementation is greedy, replacing every occurrence everywhere.
#       Maybe we should restrict the names map to target only their specific models,
#       but we might then need to know about relationships, which is complicated, eg:
#       - renaming `x_name` to `name` in a custom model:
#         if we filter records to fix based on model (eg. `mail.template` `model_id`)
#         and we have something like `object.x_name`, that's fine, but say instead
#         we have a second model that has a m2o to our custom one and does something
#         like `object.customodel.x_name`, we will fail to target that because that
#         second model is not in our filter.
#       It might be possible to achieve this maybe with the ORM fully loaded, in `end-`
def fix_renames_in_fields(cr, names_map):
    """
    Fix indirect references of renamed fields in existing records.
    These include for example: server actions, mail templates, etc.

    :param cr: the database cursor.
    :param names_map: a mapping of old to new names.
    """
    MODELS_FIELDS_TO_FIX = {
        "ir_act_server": ["code"],
        "ir_ui_view_custom": ["arch"],  # no edit_views support, should be fine anyways
        "ir_server_object_lines": ["value"],
        "mail_template": [
            "subject",
            "body_html",
            "email_from",
            "email_to",
            "partner_to",
            "email_cc",
            "reply_to",
            "scheduled_date",
            "lang",
        ],
    }
    for model, fields in MODELS_FIELDS_TO_FIX.items():
        _logger.info(
            f'Fixing {len(names_map)} renamed fields/values referenced in "{model}"'
        )
        affected_ids = set()
        for old, new in names_map.items():
            set_clauses = ", ".join(
                f"{field} = regexp_replace({field}, %(old_sub)s, %(new)s, 'g')"
                for field in fields
            )
            where_clauses = " OR ".join(
                f"{field} SIMILAR TO %(old_where)s" for field in fields
            )
            cr.execute(
                f"UPDATE {model} SET {set_clauses} WHERE {where_clauses} RETURNING id",
                dict(old_sub=fr"\m{old}\M", old_where=fr"%\m{old}\M%", new=new),
            )
            affected_ids |= {row[0] for row in cr.fetchall()}

        for field in fields:
            rename_in_translation(
                cr,
                f"{util.model_of_table(cr, model)},{field}",
                names_map,
                affected_ids,
            )


def rename_in_translation(cr, name, values_mapping, res_ids):
    """
    Apply renames in the translation values. This mostly applies to translated
    xml/html/jinja code (eg. from views, templates, mail templates).

    :param cr: the database cursor.
    :param name: the ``name`` of the translation records (eg. ``res.partner,email``)
    :param values_mapping: a mapping of old to new values to rename.
    :param res_ids: an optional collection of record ids to which the rename changes
        will be restricted to (see ``ir.translation``'s ``res_id``).
    """
    sub_expr, query_kwargs = build_chained_replace("value", values_mapping)
    where_res_ids = ""
    if res_ids:
        where_res_ids = "AND res_id IN %(res_ids)s"
        query_kwargs["res_ids"] = tuple(res_ids)
    query_kwargs["name"] = name
    cr.execute(
        f"UPDATE ir_translation SET value = {sub_expr} WHERE name = %(name)s {where_res_ids}",
        query_kwargs,
    )


def do_pending_refactors(cr):
    """
    Apply pending refactor operations (post field renames changes, etc.)

    :param cr: the database cursor.
    """
    _logger.info("Applying pending post-refactors steps")

    merged_renames: MutableMapping[str, str] = {}
    for model, field_renames in FIELD_RENAMES_PENDING.items():
        for field_old_name, field_new_name in field_renames.items():
            existing_rename_value = merged_renames.get(field_old_name)
            if existing_rename_value and existing_rename_value != field_new_name:
                raise NotImplementedError(
                    f'Rename "{field_old_name}"->"{field_new_name}" conflicts with '
                    f'existing rename "{field_old_name}"->"{existing_rename_value}". '
                    "(Current rename refactor implementation is not able to discriminate "
                    "references based on models, so all renames are merged)"
                )
            merged_renames[field_old_name] = field_new_name

    if merged_renames:
        fix_renames_in_fields(cr, merged_renames)

    FIELD_RENAMES_PENDING.clear()