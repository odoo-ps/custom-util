"""
Helper functions to manipulate views and templates.
"""
import logging

from lxml import etree

from odoo.upgrade import util

from .misc import ViewKey, WebsiteId, get_views_ids, indent_tree
from .operations import ViewOperation  # noqa: F401


__all__ = [
    "edit_views",
    "get_website_views_ids",
    "edit_website_views",
    "activate_views",
    "remove_broken_dashboard_actions",
    "cleanup_old_dashboards",
]


_logger = logging.getLogger(__name__)


def edit_views(
    cr, view_operations, verbose=True, update_arch=True, create_missing_cows=False, website_id=WebsiteId.NOTSET
):
    """
    Utility function to edit one or more views with the specified operations.

    It accepts a mapping of operations to be executed on views, with the views
    identifiers (ids, xmlids, keys) as keys and a sequence of operations as values.
    The operations must be instances of :class:`ViewOperation`.

    Since operations are not bound to any specific view, but rather just define an action
    to be taken, they can be defined beforehand in a variable and reused on multiple
    views (eg. when there are common cases to be handled).

    Examples::
        add_invisible_category_to_product_uom = AddInvisibleSiblingFields(
            name="product_uom_id", sibling_name="product_uom_category_id"
        )
        view_operations = {
            "odoo_studio_stock_pi_831629d4-a50f-4aac-9854-3211de7ea15d": (
                add_invisible_category_to_product_uom,
                RemoveFields("x_studio_date_prvue"),
            ),
            "odoo_studio_quality__70e2be7f-f2cb-41cb-bd4b-249fe9c629c0": (
                add_invisible_category_to_product_uom,
                RemoveFields("x_studio_date_prvue"),
                RemoveFields(name="date_expected"),
                RemoveFields("ups_carrier_account"),
                RemoveFields("ups_service_type"),
                RenameElements(name="put_in_pack", new_name="action_put_in_pack"),
            ),
            "odoo_studio_mrp_prod_42bdbf61-e12a-4cd9-a528-9b8504adaa4f": (
                RemoveFields("date_start_wo"),
            ),
            ViewKey("website.footer_custom", util.ref(cr, "website.default_website")): (
                AddElementsFromFile(
                    \"""//xpath[contains(@expr, "@id='footer'")]\""",
                    osp.normpath(osp.join(osp.dirname(__file__), "footer.xml")),
                    position="replace",
                ),
            ),
        }
        edit_views(cr, view_operations)

    :param cr: the database cursor.
    :type cr: psycopg2.cursor
    :param view_operations: a mapping of views identifiers (ids, xmlids, keys) to sequence of operations to
        apply to such views.
    :type view_operations: typing.Mapping[int | str | ViewKey, typing.Sequence[ViewOperation]]
    :param verbose: if True log each view being modified as INFO, otherwise the log level is set to DEBUG instead.
        Defaults to True.
    :type verbose: bool
    :param update_arch: if True set ``arch_updated`` accordingly on the edited views. This is normally wanted
        since almost always the views edited through this method are not coming from xml source files and might be
        ``noupdate`` already (eg. studio views, website COWed views, etc.), so this defaults to True.
    :type update_arch: bool
    :param create_missing_cows: COW-create missing website-specific views from "template" ones (applies only
        to views with :class:`ViewKey` identifiers). Defaults to False.
    :type create_missing_cows: bool
    :param website_id: the default ``website_id`` to use for missing COWed views when using ``create_missing_cows``.
        See also :func:`edit_website_views` for a convenience function to edit COWed website views.
    :type website_id: int | WebsiteId | None
    :rtype: None
    """
    views_ids_map = get_views_ids(
        cr,
        view_operations.keys(),
        ensure_exist=True,
        mapped=True,
        website_id=website_id,
        create_missing_cows=create_missing_cows,
    )

    updated_ids = set()
    for id_origin, operations in view_operations.items():
        if not operations:  # silently skip views with no operations
            continue
        for view_id in views_ids_map[id_origin]:
            with util.edit_view(cr, view_id=view_id, skip_if_not_noupdate=False) as arch:
                _logger.log(
                    logging.INFO if verbose else logging.DEBUG,
                    f'Patching ir.ui.view "{id_origin}" (id={view_id})',
                )
                for op in operations:
                    _logger.debug(op)
                    op(arch, cr)
                indent_tree(arch)
            updated_ids.add(view_id)

    if not updated_ids:
        _logger.warning(
            f"No views edited by `edit_views`, arguments matched these ids: {views_ids_map}",
        )
    elif update_arch:
        cr.execute("UPDATE ir_ui_view SET arch_updated = TRUE WHERE id IN %s", [tuple(updated_ids)])


def get_website_views_ids(cr, keys, website_id=WebsiteId.NOTNULL, create_missing=False):
    """
    Associate ``key``s for website views to their ``id``s, returning a mapping.

    This can be done for a specific website, or any website, but does not support multiple websites at once.
    Optionally COW-creates missing website-specific views, which requires to explicitly provide the website.

    :param cr: the database cursor.
    :type cr: psycopg2.cursor
    :param keys: an iterable of website views keys.
    :type keys: typing.Iterable[str]
    :param website_id: the website_id for which to match the views.
        Defaults to `WebsiteId.NOTNULL`, which will match any non-NULL website_id.
    :type website_id: int | WebsiteId | None
    :param create_missing: COW-create missing website-specific views from "template" ones.
    :type create_missing: bool
    :return: a mapping of keys to ids.
    :rtype: typing.MutableMapping[str, int]
    :raise ValueError: if ``create_missing`` is specified but not ``website_id``.
    """
    if create_missing and not isinstance(website_id, int):
        raise ValueError('Must specify a "website_id" when using "create_missing"')

    view_keys = [ViewKey(key, website_id) if isinstance(key, str) else key for key in keys]
    keys_to_id_map = {
        view_key.key: view_id
        for view_key, view_ids in get_views_ids(
            cr, keys=view_keys, website_id=website_id, create_missing_cows=create_missing, mapped=True
        )
        for (view_id,) in (view_ids,)  # raise if len != 1, don't look too close
    }

    return keys_to_id_map


def edit_website_views(cr, view_operations, website_id=WebsiteId.NOTNULL, create_missing=False, verbose=True):
    """
    Edit one or more website views with the specified operations.

    This is a wrapper of :func:`edit_views` that expects website views ``key``s
    as keys for the ``view_operations`` dict.

    :param cr: the database cursor.
    :type cr: psycopg2.cursor
    :param view_operations: a mapping of website views keys (or ids, or :class:`ViewKey`s) to a sequence
        of operations to apply to the corresponding view.
        N.B. contrary to :func:`edit_views`, strings will be interpreted as keys instead of xmlids.
    :type view_operations: typing.Mapping[int | str | ViewKey, typing.Sequence[ViewOperation]]
    :param website_id: the ``website_id`` for which to match the views. Defaults to `WebsiteId.NOTNULL`,
        which will match any non-NULL website. You will need to to explicitly provide this argument
        if the db is multi-website or if using ``create_missing``.
    :type website_id: int | WebsiteId | None
    :param create_missing: COW-create missing website-specific views from "template" ones.
    :type create_missing: bool
    :param verbose: same as :func:`edit_views` ``verbose`` argument.
    :type verbose: bool
    :rtype: None
    """
    view_operations = {
        ViewKey(key, website_id) if isinstance(key, str) else key: operations
        for key, operations in view_operations.items()
    }
    edit_views(
        cr, view_operations, verbose, update_arch=True, website_id=website_id, create_missing_cows=create_missing
    )


def activate_views(cr, ids_or_xmlids=None, *more_ids_or_xmlids, ids=None, xmlids=None):
    """
    Utility function to activate one or more views.
    Accepts the same arguments as :func:`get_views_ids`.

    Does some basic sanity check that all the given ids/xmlids exist in the database,
    otherwise will log an error about it, which might even make an SH build because of it.
    """
    ids = get_views_ids(cr, ids_or_xmlids, *more_ids_or_xmlids, ids=ids, xmlids=xmlids)

    # check if all ids exist in ir_ui_view
    cr.execute("SELECT id FROM ir_ui_view WHERE id in %s;", (tuple(ids),))
    ids_in_ir_ui_view = set(row[0] for row in cr.fetchall())
    if ids > ids_in_ir_ui_view:
        _logger.error(
            'Some views ids do not exist in "ir_ui_view". '
            'Possibly passed wrong ids or "ir_model_data" in inconsistent state? '
            f"Missing ids: {ids - ids_in_ir_ui_view}"
        )

    cr.execute(
        """
        UPDATE ir_ui_view SET active = TRUE
        WHERE id IN %s
        AND active = FALSE
        RETURNING id;
        """,
        (tuple(ids),),
    )
    activated_views = set(row[0] for row in cr.fetchall())
    if activated_views != ids:
        _logger.info(f"Tried to activate views that were already active: {ids - activated_views}")
    _logger.debug(f"Activated views: {activated_views}")
    return activated_views


def remove_broken_dashboard_actions(cr, broken_elements_xpaths, views_ids=None):
    """
    Removes matched elements from the dashboard views (``ir.ui.view.custom``).
    Useful to delete invalid saved views/filters in the dashboard that cannot be
    removed by the user through the UI (usually because of JS errors).

    :param cr: the database cursor object.
    :param broken_elements_xpaths: an iterable of xpaths to match in the dashboard views
        xml arch for the elements to remove.
    :param views_ids: apply only on the specified (``ir.ui.view.custom``) ids
        instead of all the dashboard views.
    """
    _logger.info("Fixing/removing broken dashboard actions")
    env = util.env(cr)
    if views_ids:
        boards_views = env["ir.ui.view.custom"].browse(views_ids)
    else:
        boards_ref_view = env.ref("board.board_my_dash_view")
        boards_views = env["ir.ui.view.custom"].search([("ref_id", "=", boards_ref_view.id)])
    for board_view in boards_views:
        xml = etree.fromstring(board_view.arch)
        for xpath in broken_elements_xpaths:
            for element in xml.xpath(xpath):
                element.getparent().remove(element)
        board_view.arch = etree.tostring(xml, encoding="unicode")


def cleanup_old_dashboards(cr):
    """
    Dashboard views records are created using Copy-on-Write (COW) and in the actual
    view only the last one is used (greatest create_date). All other versions remain
    in the database untouched and unused (but allowing the user to revert to a previous
    version in case of errors).
    This function removes all dashboard views for each user except for the most recent one.

    :param cr: the database cursor object.
    """
    # Delete obsolete (COW) dashboard records
    _logger.info("Cleaning up obsolete dashboard records (COW)")
    cr.execute(
        """
        WITH sorted_dashboards AS (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY user_id, ref_id
                                          ORDER BY create_date DESC) AS row_no
              FROM ir_ui_view_custom
        )
        DELETE FROM ir_ui_view_custom iuvc
         WHERE EXISTS (SELECT 1
                         FROM sorted_dashboards d
                        WHERE d.id = iuvc.id AND d.row_no > 1)
        """
    )
    _logger.info(f"Deleted {cr.rowcount} old dashboard views")
