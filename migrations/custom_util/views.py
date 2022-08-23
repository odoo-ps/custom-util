"""
Utility functions and classes to perform common migration operations on views
and other xml documents in a database.
"""
import enum
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Collection, Pattern, Sequence

from lxml import etree

from odoo.upgrade import util

from .helpers import get_ids


__all__ = [
    "WebsiteId",
    "ViewKey",
    "get_views_ids",
    "edit_views",
    "create_cow_views",
    "create_cow_view",
    "get_website_views_ids",
    "edit_website_views",
    "activate_views",
    "get_arch",
    "extract_elements",
    "extract_elements_from_view",
    "indent_tree",
    "ViewOperation",
    "XPathOperation",
    "AddElementPosition",
    "AddElements",
    "AddElementsFromFile",
    "CopyElements",
    "RemoveElements",
    "RemoveFields",
    "AddInvisibleSiblingFields",
    "RenameElements",
    "UpdateAttributes",
    "ReplacePosition",
    "ReplaceValue",
    "MoveElements",
    "remove_broken_dashboard_actions",
    "cleanup_old_dashboards",
]


_logger = logging.getLogger(__name__)


class WebsiteId(enum.Enum):
    """Enums for representing special values for views ``website_id``"""

    NOTSET = "NOTSET"
    NOTNULL = "NOTNULL"


class ViewKey:
    """
    Class that represents references to views by their key and website_id, and provides utility methods to convert
    the references to the views ids.

    :param key: the view key.
    :type key: str
    :param website_id: the ``website_id`` associated with the view key. Use `WebsiteId.NOTSET` to match
        any ``website_id``, `WebsiteId.NOTNULL` to match any ``website_id`` that is an integer id,
        or `None` to match views with no ``website_id`` (ie. "template views" not associated to any website).
        Defaults to `WebsiteId.NOTSET`.
    :type website_id: int | WebsiteId | None
    """

    def __init__(self, key, website_id=WebsiteId.NOTSET):
        self.key = key
        self.website_id = website_id

    @classmethod
    def get_all_ids(cls, cr, keys, must_exist=False, same_website=False):
        """
        Batch-dereference a bunch of instances to the matching view ids in the databasee.

        :param cr: the database cursor.
        :type cr: psycopg2.cursor
        :param keys: the :class:`ViewKey`s to dereference.
        :type keys: ViewKey | typing.Iterable[ViewKey]
        :param must_exist: if True, check that all the instances match an existing view in the database.
        :type must_exist: bool
        :param same_website: if True, check that all matched views belong to the same website.
        :type same_website: bool
        :return: a mapping of :class:`ViewKey`s to the matched view `ids` in the database.
        :rtype: typing.MutableMapping[ViewKey, typing.Set[int]]
        """
        if isinstance(keys, ViewKey):
            keys = [keys]

        cls_name = ViewKey.__name__

        # SQL NULL is not comparable (ie. `NULL = NULL` => NULL, not TRUE), and there's
        # no value that always compares to TRUE, so we need 3 separate WHERE clauses
        where_clauses = defaultdict(list)
        for view_key in keys:
            if not isinstance(view_key, ViewKey):
                raise TypeError(f"`keys` must be instances of {cls_name}")
            elif isinstance(view_key.website_id, int):
                where_clauses["(key, website_id) IN %s"].append((view_key.key, view_key.website_id))
            elif view_key.website_id is None:
                where_clauses["(key IN %s AND website_id IS NULL)"].append(view_key.key)
            elif view_key.website_id is WebsiteId.NOTNULL:
                where_clauses["(key IN %s AND website_id IS NOT NULL)"].append(view_key.key)
            elif view_key.website_id is WebsiteId.NOTSET:
                where_clauses["key IN %s"].append(view_key.key)
            else:
                raise ValueError(f"Unhandled `{cls_name}.website_id` value: {view_key}")

        cr.execute(
            f"SELECT id, key, website_id FROM ir_ui_view WHERE {' OR '.join(where_clauses.keys())}",
            tuple(tuple(where_clause_list) for where_clause_list in where_clauses.values()),
        )
        res_by_id = {id_: (key, website_id) for id_, key, website_id in cr.fetchall()}
        ids_by_key = {
            vk: set(id_ for id_, (view_key, website_id) in res_by_id.items() if vk.matches(view_key, website_id))
            for vk in keys
        }

        unmatched_ids = res_by_id.keys() - set(id_ for ids in ids_by_key.values() for id_ in ids)
        assert not unmatched_ids, f"Query returned ids that don't match any {cls_name}: {unmatched_ids}"

        keys_by_website = {
            website_id: set(vk for vk, ids in ids_by_key.items() if id_ in ids)
            for id_, (_, website_id) in res_by_id.items()
        }
        for view_key in keys:  # sanity check
            if not isinstance(view_key.website_id, int):
                continue
            matching_websites = {website_id for website_id, vkeys in keys_by_website.items() if view_key in vkeys}
            assert len(matching_websites) <= 1, f"{view_key} matches more than one website: {matching_websites}"

        if same_website and len(keys_by_website) > 1:
            raise ValueError(f"Matched views for the specified keys on multiple websites: {keys_by_website}")

        if must_exist:
            missing = [vk for vk, ids in ids_by_key.items() if not ids]
            if missing:
                raise KeyError(f"Some {cls_name} did not match in the db: {missing}")

        return ids_by_key

    def get_ids(self, cr, **kwargs):
        """
        Dereference the instance to the matching view ids in the database.

        :param cr: the database cursor.
        :type cr: psycopg2.cursor
        :param kwargs: additional keyword arguments for :meth:`~.get_all_ids`.
        :type kwargs: any
        :return: the matched ids for the instance.
        :rtype: typing.Set[int]
        """
        (ids,) = self.get_all_ids(cr, self, **kwargs).values()
        return ids

    def __hash__(self):
        return hash((self.__class__, self.key, self.website_id))

    def matches(self, key, website_id):
        """
        Check if the instance matches a specific key, website_id pair

        :param key: the ``key`` value to match against.
        :type key: str
        :param website_id: the ``website_id`` value to match against.
        :type website_id: int | None
        :return: True if the values match the instance, False otherwise.
        :rtype: bool
        """
        if self.website_id is None or isinstance(self.website_id, int):
            return self == ViewKey(key, website_id)
        elif self.website_id is WebsiteId.NOTNULL:
            return key == self.key and self.website_id is not None
        elif self.website_id is WebsiteId.NOTSET:
            return key == self.key
        return False

    def __eq__(self, other):
        if isinstance(other, ViewKey):
            return (other.key, other.website_id) == (self.key, self.website_id)
        elif isinstance(other, Sequence) and len(other) == 2:
            return self.matches(*other)
        return False

    def __repr__(self):
        return f"{self.__class__.__name__}({repr(self.key)}, {repr(self.website_id)})"


def get_views_ids(
    cr,
    views=None,
    *more_views,
    ids=None,
    xmlids=None,
    keys=None,
    website_id=WebsiteId.NOTSET,
    create_missing_cows=False,
    ensure_exist=True,
    mapped=False,
):
    """
    Get views ids from the given arguments.

    The function accepts xmlids, ids and keys arguments in a variety of ways; these can be
    freely mixed and will be merged together for the final returned result.

    :param cr: the database cursor.
    :type cr: psycopg2.cursor
    :param views: an id as `int`, xmlid as `str`, key as :class:`ViewKey`, or a collection of these.
    :type views: int | str | ViewKey | typing.Collection[int | str | ViewKey]
    :param more_views: more ids, xmlids or keys provided as positional arguments.
    :type more_views: int | str | ViewKey
    :param ids: an id or collection of ids as `int`s. Will be returned together
        with other fetched ids.
    :type ids: int | typing.Collection[int]
    :param xmlids: an xmlid or collection of xmlids as `str`s, whose ids will be fetched.
    :type xmlids: str | typing.Collection[str]
    :param keys: a key or collection of keys as `str` or :class:`ViewKey` instances,
        that will be dereferenced to the matching ids.
    :type keys: str | ViewKey | typing.Collection[str | ViewKey]
    :param website_id: the default ``website_id`` value to use for view keys passed as `str`s that will be converted
        internally to :class:`ViewKey`.
    :type website_id: int | WebsiteId | None
    :param create_missing_cows: COW-create missing website-specific views for the given keys from "template" ones.
        Defaults to False.
    :type create_missing_cows: bool
    :param ensure_exist: if True, check that all the passed values match existing views records in the database.
        Defaults to True.
    :type ensure_exist: bool
    :param mapped: if True, return a mapping of the passed values to matched ids, otherwise a set of all matched ids.
        Defaults to False.
    :type mapped: bool
    :return: the matched view ids for the specified arguments, as dict or set, depending on the ``mapped`` argument.
    :rtype: typing.Set[int] | typing.MutableMapping[int | str | ViewKey, typing.Set[int]]
    :raise TypeError: if ``create_missing_cows`` is used with invalid ``website_id``s.
    """
    view_keys = set()

    def get_view_keys(value, coerce_str=False):
        nonlocal view_keys
        if isinstance(value, ViewKey):
            view_keys.add(value)
            return None
        if coerce_str and isinstance(value, str):
            view_keys.add(ViewKey(key, website_id))
            return None
        if isinstance(value, Collection) and not isinstance(value, str):
            # recurse to add view_keys to set and convert them to None in the iterable
            value = [get_view_keys(v, coerce_str=coerce_str) for v in value]
            return [v for v in value if v is not None]
        return value

    get_view_keys(keys, coerce_str=True)
    assert not keys, "all keys should have been consumed, otherwise got unhandled types"
    views = get_view_keys(views)
    more_views = get_view_keys(more_views)

    if any((views, more_views, ids, xmlids)):
        result = get_ids(
            cr, views, *more_views, model="ir.ui.view", ids=ids, xmlids=xmlids, ensure_exist=ensure_exist, mapped=mapped
        )
    else:
        result = {} if mapped else set()

    if view_keys:
        ids_by_key = ViewKey.get_all_ids(
            cr, view_keys, same_website=create_missing_cows, must_exist=ensure_exist and not create_missing_cows
        )

        if create_missing_cows:
            missing_keys_by_website = defaultdict(set)
            for view_key, ids in list(ids_by_key.items()):
                if not ids:
                    if view_key.website_id in (WebsiteId.NOTNULL, WebsiteId.NOTSET):
                        if not isinstance(website_id, int):
                            raise TypeError(f"Tried using `create_missing_cows` without `website_id`: {view_key}")
                        key_website_id = website_id
                    elif isinstance(view_key.website_id, int):
                        key_website_id = view_key.website_id
                    else:
                        assert view_key.website_id is None
                        raise TypeError(f"Cannot use `create_missing_cows` with `website_id=None` in {view_key}")
                    missing_keys_by_website[key_website_id].add(view_key.key)
                    ids_by_key.pop(view_key)

            for cow_website_id, missing_keys in missing_keys_by_website.items():
                for key, cow_id in create_cow_views(cr, missing_keys, cow_website_id).items():
                    ids_by_key[ViewKey(key, cow_website_id)] = {cow_id}

        if mapped:
            result.update(ids_by_key)
        else:
            result |= {id_ for ids in ids_by_key.values() for id_ in ids}

    return result


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


def create_cow_views(cr, keys, website_id):
    """
    Creates COWed views from their "template" ones for the given website and returns their ids.
    If a COWed view already exists, it will be reused instead.

    :param cr: the database cursor.
    :type cr: psycopg2.cursor
    :param keys: the key of the website view to COW.
    :type keys: str | typing.Sequence[str]
    :param website_id: the website id where to create the COWed views on.
    :type website_id: int
    :return: a mapping of ids to keys for the COWed views.
    :rtype: typing.MutableMapping[str, int]
    :raise RuntimeError: if the "website" module is not yet loaded in the ORM/registry.
    :raise KeyError: if any "template" views for the given keys is not found.
    """
    if isinstance(keys, str):
        keys = [keys]

    # Ensure keys are ordered such that parents are before their children.
    # Avoids children being copied and deleted when a parent is COWed.
    # Also filter website_id so that we only match template views for next sanity check.
    cr.execute(
        """
        WITH RECURSIVE parents (id, path) AS (
            SELECT row.id, row.id::text FROM ir_ui_view row WHERE row.inherit_id IS NULL
             UNION
            SELECT row.id, pp.path || '/' || row.id
              FROM ir_ui_view row, parents pp
             WHERE row.inherit_id = pp.id
        )
           SELECT iuv.key
             FROM ir_ui_view iuv
        LEFT JOIN parents p ON iuv.id = p.id
            WHERE key IN %s AND iuv.website_id IS NULL
         ORDER BY p.path
        """,
        (tuple(keys),),
    )
    sorted_keys = [key for [key] in cr.fetchall()]
    missing_template_keys = set(keys) - set(sorted_keys)
    if missing_template_keys:
        raise KeyError(f"Some of the specified keys have no matching view without website_id: {missing_template_keys}")

    env = util.env(cr)
    if "website" not in env.registry._init_modules:
        raise RuntimeError('"website" module must be already loaded in the registry to use this function')
    View = env["ir.ui.view"]

    ids_by_key = {}
    for key in sorted_keys:
        std_view = View.search([("key", "=", key), ("website_id", "=", False)])
        assert std_view, f'No "template" view found with key "{key}" and no "website_id"'

        std_view.with_context(website_id=website_id).write({"key": key})  # COW here
        cow_view = View.search([("key", "=", key), ("website_id", "=", website_id)])
        assert cow_view, f"cowed view doesn't exist ({key}, {website_id})"

        ids_by_key[key] = cow_view.id

    return ids_by_key


def create_cow_view(cr, key, website_id):  # backward compatibility
    """
    Creates a COWed view from the "template" one for the given website and returns its id.
    If a COWed view already exists, its id will be returned instead.

    :param cr: the database cursor.
    :type cr: psycopg2.cursor
    :param key: the key of the view.
    :type key: str
    :param website_id: the website id where to create/return the COWed view.
    :type website_id: int
    :return: the id of the COWed view.
    :rtype: int
    """
    (cow_id,) = create_cow_views(cr, key, website_id).values()
    return cow_id


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


def get_arch(cr, view):
    """
    Get the parsed arch for a view in the database.

    :param cr: the database cursor.
    :param view: a view id or xmlid.
    :return: the parsed arch as :class:`etree.ElementTree` xml.
    :raise ValueError: if the view was not found or its arch is empty.
    """
    [view_id] = get_views_ids(cr, view)
    arch_col = "arch_db" if util.column_exists(cr, "ir_ui_view", "arch_db") else "arch"
    cr.execute(
        "SELECT {arch} FROM ir_ui_view WHERE id=%s".format(arch=arch_col),
        [view_id],
    )
    [arch] = cr.fetchone() or [None]
    if not arch:
        raise ValueError(f'View "{view}" not found, or has no arch')
    return etree.fromstring(arch)


def extract_elements(arch, xpaths, view_name=None):
    """
    Extract the elements in the given xpaths from the provided arch.

    :param arch: a parsed xml as :class:`etree.ElementTree` (eg. a view arch).
    :param xpaths: one or multiple xpaths used to match and extract the elements.
    :param view_name: logging-related param to specify an identifier for the view.
    :return: the extracted elements as str. N.B. because of possibly multiple matched
        elements, the returned string might not be valid xml unless wrapped in a root tag.
    :raise ValueError: if the xpaths did not match any elements to extract.
    """
    if isinstance(xpaths, str):
        xpaths = [xpaths]

    extracted_elements = []
    for xpath in xpaths:
        for element in arch.xpath(xpath):
            extracted_elements.append(etree.tostring(element, encoding=str))

    if not extracted_elements:
        view_name_msg = f"view {view_name}" if view_name else "arch"
        raise ValueError(f"No elements found in {view_name_msg} with xpaths: {xpaths}")

    return "\n".join(extracted_elements)


def extract_elements_from_view(cr, view, xpaths):
    """
    Get a view arch and extract elements from it.
    See :func:`get_arch` and :func:`extract_elements` for more info.
    """
    return extract_elements(get_arch(cr, view), xpaths, view_name=view)


def indent_tree(elem, level=0):
    """
    Reindents / reformats an xml fragment.

    The `lxml` library doesn't `pretty_print` xml tails, this method aims
    to solve this.

    :param elem: the XML element tree to be formatted.
    :type elem: :class:`etree.Element`
    :param level: depth of indentation of the root element (2-spaces), defaults to 0.
    :type level: int, optional
    :return: the XML element tree, with properly indented text and tail.
    :rtype: :class:`etree.Element`
    """
    # See: http://lxml.de/FAQ.html#why-doesn-t-the-pretty-print-option-reformat-my-xml-output
    # Below code is inspired by http://effbot.org/zone/element-lib.htm#prettyprint
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for subelem in elem:
            indent_tree(subelem, level + 1)
        if not subelem.tail or not subelem.tail.strip():
            subelem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i
    return elem


class ViewOperation(ABC):
    """
    Abstract base class for operations to be performed on views/xml documents.
    """

    @abstractmethod
    def __call__(self, arch, cr=None):
        """
        Abstract method with the actual implementation of the logic of the operation.

        :param arch: `lxml.etree` representing the architecture of the document.
        :param cr: the database cursor if needed by the implementation.
        """

    def on(self, arch, cr=None):
        """
        Many elegance, much wow: `ViewOperation.on(arch)`.
        Just a more semantically meaningful shortcut for :func:`__call__`.
        """
        return self(arch, cr)

    def __iter__(self):
        # TODO: are we sure we want this?
        # if single operation is performed and operation is not in enclosed in an iterable
        yield self


class XPathOperation(ViewOperation, ABC):
    """
    Abstract base class for operations that use XPath selectors to match elements.

    The xpaths are pre-compiled to improve performance and for early validation
    and debugging.

    :param xpaths: the xpath(s) as a `str` or an iterable of `str`.
    :raise XPathSyntaxError: if one of the xpaths is invalid.
    """

    def __init__(self, xpaths):
        if isinstance(xpaths, str):
            xpaths = [xpaths]
        self.xpaths = list(self.generate_xpaths(xpaths))

    def get_elements(self, arch):
        """
        Yields all elements found by all (compiled) xpaths.

        :raises ValueError: if something other than xml elements is found.
        :yield: :class:`etree.Element` matching the xpath.
        """

        for xpath in self.xpaths:
            elements = xpath(arch)
            # Check return value https://lxml.de/xpathxslt.html#xpath-return-values
            if not isinstance(elements, list):
                raise ValueError(f'XPath expression "{xpath}" does not yield elements. Got: {elements}')
            if not elements:
                _logger.warning(f"XPath expression {xpath} yielded no results")

            for el in elements:
                yield el

    @staticmethod
    def generate_xpaths(xpaths):
        """
        Compiles xpath `str` into :class:`etree.XPath` objects.

        :param xpaths: `str` or an iterable of `str`
        :yield: :class:`etree.XPath` object for each xpath `str`
        """
        for xpath in xpaths:
            try:
                yield etree.XPath(xpath)
            except etree.XPathSyntaxError:
                _logger.error(f"Error evaluating xpath: {xpath}")
                raise


class AddElementPosition(enum.Enum):
    """Enums of valid position for adding new elements to a view."""

    INSIDE = "adds the new element(s) inside the matched one, as last children"
    AFTER = "adds the new element(s) as next sibling(s) of the matched one"
    BEFORE = "adds the new element(s) as previous sibling(s) of the matched one"
    REPLACE = "replaces the matched element with the new one(s)"


class AddElements(XPathOperation):
    r"""
    Adds new elements to the view at the matched xpath nodes, with the specified position.

    Examples::
        AddElements("//data", '<field name="company_id"/>')

        AddElements('//field[@name="id"]', '<field name="user_id"/>', position="after")

        AddElements(
            \"""//xpath[contains(@expr, "field[@name='name']")]\""",
            \"""
            <xpath expr="//field[@name='lst_price']" position="attributes">
                <attribute name="invisible">1</attribute>
            </xpath>
            \""",
            position="before"
        )

    :param xpaths: an xpath `str` or an iterable of xpaths.
        See :class:`XPathOperation` for additional info about this argument.
    :param elements_xml: a xml fragment string of one or more valid xml elements.
    :param position: a member of :class:`AddElementPosition` or its name string.
        See :class:`AddElementPosition` for the acceptable values.
        Defaults to ``inside`` if omitted.
    :raise ValueError: if the specified ``position`` is not a valid one.
    """

    def __init__(self, xpaths, elements_xml, position=AddElementPosition.INSIDE):
        super().__init__(xpaths)
        if not isinstance(position, AddElementPosition):
            try:
                position = AddElementPosition[position.upper()]
            except KeyError as exc:
                raise ValueError(
                    f'"position" must be one of {",".join(e.name for e in AddElementPosition)}, got "{position}"'
                ) from exc
        self.position = position
        if elements_xml:
            self.elements_xml = elements_xml

    def __str__(self):
        return (
            f"Add elements `{self.elements_xml}` at XPath(s) `{self.xpaths}` (position: `{self.position.name.lower()}`)"
        )

    @property
    def elements_xml(self):
        """Get the original xml string of the elements being added"""
        return self._elements_xml

    @elements_xml.setter
    def elements_xml(self, value):
        """Set the xml elements being added from a string"""
        self.elements = self._prepare_elements(value)
        self._elements_xml = value

    @staticmethod
    def _prepare_elements(elements_xml):
        """
        Converts an xml fragment into a sequence of :class:`etree.ElementBase`.
        """
        # since we don't know if we got one or more elements (as siblings) in the
        # given xml, and xml needs one (and only one) root, provide it here.
        elements_doc_str = "<xmlfragment>" + elements_xml + "</xmlfragment>"
        elements_doc = etree.fromstring(elements_doc_str)
        elements = list(elements_doc)
        if not elements:
            raise ValueError(f"Invalid xml provided: {elements_xml}")
        return elements

    def __call__(self, arch, cr=None):
        Pos = AddElementPosition  # better readability
        for el in self.get_elements(arch):
            if self.position in (Pos.INSIDE, Pos.BEFORE):
                for new_el in self.elements:
                    if self.position is Pos.INSIDE:
                        el.append(new_el)
                    elif self.position is Pos.BEFORE:
                        el.addprevious(new_el)
            elif self.position in (Pos.AFTER, Pos.REPLACE):
                for new_el in reversed(self.elements):
                    el.addnext(new_el)
                if self.position is Pos.REPLACE:
                    el.getparent().remove(el)


class AddElementsFromFile(AddElements):
    r"""
    Adds new elements to the view at the matched xpath nodes, loading them from
    an external xml file, optionally picking only some elements from it.

    Examples::
        AddElementsFromFile(
            \"""//xpath[contains(@expr, "@id='footer'")]\""",
            osp.normpath(osp.join(osp.dirname(__file__), "footer.xml")),
            position="replace",
        )

    :param xpaths: an xpath `str` or an iterable of xpaths.
        See :class:`XPathOperation` for additional info about this argument.
    :param filename: the xml file from which to load the new elements.
    :param source_xpaths: an xpath specifying the nodes to extract from the loaded xml
        that will be added to the view being processed. Defaults to the root tag.
    :param kwargs: additional keyword arguments for :class:`AddElements`.
        N.B. arguments ``xpaths`` and ``elements_xml`` are already provided.
    """

    def __init__(self, xpaths, filename, source_xpaths="/*", **kwargs):
        self.source_xpaths = source_xpaths
        with open(filename, "rb") as fp:
            arch = etree.fromstring(fp.read())
        elements_xml = extract_elements(arch, source_xpaths, view_name=filename)
        super().__init__(xpaths, elements_xml, **kwargs)

    def __str__(self):
        return (
            f"Add elements from file `{self.filename}` matching XPath(s) `{self.source_xpaths}` "
            f"(source) at `{self.xpaths}` (target)"
        )


class CopyElements(AddElements):
    """
    Copies elements from other parts of the view, or from another view altogether.

    Examples::
        CopyElements("//*[@id='source_element']", "//*[@id='dest_element']")

        CopyElements(
            "//div[@id='footer']",
            "//div[@id='footer']",
            from_view="ailouvain_website.footer_default",
            position="replace",
        )

    :param source_xpaths: the xpaths to extract the elements from the source view.
    :param xpaths: the xpaths of the destination elements to add the extracted ones to.
    :param from_view: if provided, specifies the id or xmlid of the view from which
        to copy the elements from, otherwise it's the same one of the operation.
    :param kwargs: additional keyword arguments for :class:`AddElements`.
        N.B. arguments ``xpaths`` and ``elements_xml`` are already provided.
    """

    def __init__(self, source_xpaths, xpaths, from_view=None, **kwargs):
        super().__init__(xpaths, None, **kwargs)
        self.source_xpaths = source_xpaths
        self.source_view = from_view

    def __call__(self, arch, cr=None):
        if self.source_view and not cr:
            raise RuntimeError("Cannot copy elements from other views without cursor")
        source_arch = get_arch(cr, self.source_view) if self.source_view else arch
        self.elements_xml = extract_elements(source_arch, self.source_xpaths, view_name=self.source_view)
        super().__call__(arch, cr)

    def __str__(self):
        result_str = (
            f"Copy elements matching XPath(s) `{self.source_xpaths}` to elements matching XPath(s) `{self.xpaths}`"
        )
        if self.source_view is None:
            return result_str
        else:
            return f"{result_str} from view {self.source_view}"


class RemoveElements(XPathOperation):
    """
    Removes all elements from the view with matching xpaths.

    Keep in mind that the structure of the document is changed, which may affect
    other xpath selectors, so, usually, removing Elements should be the last operation.

    Examples::
        RemoveElements("//*")

        RemoveElements([f"//data/xpath[{i}]" for i in (12, 13, 14)])
    """

    def __call__(self, arch, cr=None):
        for el in self.get_elements(arch):
            el.getparent().remove(el)

    def __str__(self):
        return f"Remove all elements matching XPath(s) `{self.xpaths}`"


class RemoveFields(RemoveElements):
    """
    Removes all ``<field>`` elements in a view that match the given ``name``s.

    Examples::
        RemoveFields(["state", "id"])

    :param names: the elements names as a `str` or an iterable of `str`
    """

    def __init__(self, names):
        if isinstance(names, str):
            self.names = [names]
        else:
            self.names = names
        self.xpaths = [f'//field[@name="{name}"]' for name in self.names]
        super().__init__(self.xpaths)

    def __str__(self):
        return f"Remove all fields with name(s) `{self.names}`"


class AddInvisibleSiblingFields(AddElements):
    """
    Adds an invisible ``<field>`` element as sibling of the specified ``<field>``.
    The new element will be inserted after the matched element.

    Examples::
        AddInvisibleSiblingFields("uom_id", "product_uom_category_id")

    :param name: the ``name`` attribute to match of the ``<field>`` element to which
        the new hidden ``<field>`` element will be added as sibling.
    :param sibling_name: the ``name`` attribute of the new invisible ``<field>``
        element to add to the xml.


    """

    def __init__(self, name, sibling_name, position="after"):
        # make sure we don't add siblings where they already exist
        self.name = name
        self.sibling_name = sibling_name
        super().__init__(
            f'//field[@name="{name}" and not(../field[@name="{sibling_name}"])]',
            f'<field name="{sibling_name}" invisible="1" />',
            position=position,
        )

    def __str__(self):
        return f"Add invisible sibling `field[@name='{self.sibling_name}']` to all `field[@name='{self.name}']`"


class RenameElements(XPathOperation):
    """
    Renames the value of the attribute ``name`` for all matching elements in a view.
    Especially useful in case a field of a model was renamed.

    Examples::
        RenameElements("payment_term_id", "invoice_payment_term_id")

    :param name: a `str` of the current ``name`` to match.
    :param new_name: a `str` of the new replacement ``name``.
    """

    def __init__(self, name, new_name, xpath="//*"):
        super().__init__(f'{xpath}[@name="{name}"] | {xpath}[@name="{name}"]/../label[@for={name}]')
        self.name = name
        self.new_name = new_name

    def __call__(self, arch, cr=None):
        for el in self.get_elements(arch):
            if el.tag == "label":
                el.attrib["for"] = self.new_name
            else:
                el.attrib["name"] = self.new_name

    def __str__(self):
        return f"Update `name` attribute: `{self.name}` -> `{self.new_name}` (XPath(s): `{self.xpaths}`)"


class UpdateAttributes(XPathOperation):
    """
    Updates attributes of the elements matched by the given xpath.
    The attributes and their values can be specified the same way the `dict` constructor
    accepts arguments, meaning as a dict, an iterable of ``(key, value)`` tuples,
    or as keyword arguments.
    Given attributes with `None` as value will be remove from the element(s).

    Examples::
        UpdateAttributes(
            \"\"\"//xpath[contains(@expr, "field[@name='name']")]\"\"\",
            expr="//field[@name='name']",
        )

        UpdateAttributes(
            "//xpath[contains(@expr, 'item_ids')][3]",
            {"expr": '//page[@name="pricelist_config"]/group', "position": "after"},
        )

        UpdateAttributes(
            "//field[@name='asset_id']",
            invisible=None,
        )

    :param xpaths: an xpath `str` or an iterable of xpaths.
        See :class:`XPathOperation` for additional info about this argument.
    :param *dict_args: attributes specification as positional arguments for `dict`.
    :param **dict_kwargs: attributes specification as keyword arguments for `dict`.
    :raise AttributeError: if no attributes are specified.
    """

    def __init__(self, xpaths, *dict_args, **dict_kwargs):
        super().__init__(xpaths)
        attrs_dict = dict(*dict_args, **dict_kwargs)
        if not attrs_dict:
            raise AttributeError("Must provide at least one attribute")
        self.attrs_dict = attrs_dict

    def __call__(self, arch, cr=None):
        for el in self.get_elements(arch):
            for attr_name, new_value in self.attrs_dict.items():
                if new_value is None:
                    del el.attrib[attr_name]
                else:
                    el.attrib[attr_name] = new_value

    def __str__(self):
        return f"Update attributes: `{self.attrs_dict}` (XPath(s): `{self.xpaths}`)"


# TODO: add some convenience operation to fixup xpath expr.
#       Very common case in studio views where the referenced fields are invalid.


class ReplacePosition(enum.Flag):
    """Search-and-replace position flags for :class:`ReplaceValue` operation"""

    ATTRIBUTES = enum.auto()
    TEXT = enum.auto()
    ANY = ATTRIBUTES | TEXT


class ReplaceValue(XPathOperation):
    r"""
    Replaces occurrences of text in elements matched by the given xpath or in all elements.

    Examples::
        ReplaceValue("date_invoice", "invoice_date")

        ReplaceValue("date_invoice", "invoice_date", position=ReplacePosition.ANY)

        ReplaceValue(re.compile("name|number"), "reference", position="TEXT")

        ReplaceValue(
            "number",
            "name",
            xpaths=\"""//xpath[contains(@expr, "field[@name='number']")]\"""
        )

    :param pattern: The value to search and replace.
        It will replace *all* the occurrences of the str/pattern in the elements matched.
    :type pattern: `str` or :class:`Pattern` (compiled regex pattern)
    :param repl: The new value to assign.
        A replacement pattern can be used (same as the ones used in `re.sub`)
        if the ``pattern`` argument provided is a compiled regex.
    :type repl: `str`
    :param xpaths: One or multiple xpaths to search for elements. Defaults to `"//*"`.
        See :class:`XPathOperation` for additional info about this argument.
    :type xpaths: `str` or `Collection[str]`
    :param position: Position in the xml elements where to search and replace.
        Defaults to `ReplacePosition.ATTRIBUTE`.
        Accepted options:
        - ``ATTRIBUTES``: searches and replaces inside the matched elements attributes.
        - ``TEXT``: searches and replaces inside the matched elements text.
        - ``ANY``: both of the above.
    :type position: :class:`ReplacePosition`, or a `str` with the flag name.
    """

    def __init__(self, pattern, repl, xpaths="//*", position=ReplacePosition.ATTRIBUTES):
        super().__init__(xpaths)
        self.pattern = pattern
        self.repl = repl
        if not isinstance(position, ReplacePosition):
            try:
                position = ReplacePosition[position.upper()]
            except KeyError as exc:
                raise ValueError(
                    f'"position" must be one of {",".join(e.name for e in ReplacePosition)}, got "{position}"'
                ) from exc
        self.position = position

    def __call__(self, arch, cr=None):
        def match_and_replace(pattern, repl, value):
            """
            Tries to replace `value` with `repl` if it matches `pattern`.
            Supports compiled regex for `pattern` or plain strings.
            Returns the replaced value, or None if nothing matched.
            """
            if not value:
                return None
            if isinstance(pattern, Pattern):
                if not pattern.search(value):
                    return None
                return pattern.sub(repl, value)
            if pattern in value:
                return value.replace(pattern, repl)
            return None

        for el in self.get_elements(arch):
            if self.position & ReplacePosition.ATTRIBUTES:
                for attr_name, attr_value in el.attrib.items():
                    new_value = match_and_replace(self.pattern, self.repl, attr_value)
                    if new_value is not None:
                        el.attrib[attr_name] = new_value
            if self.position & ReplacePosition.TEXT:
                new_value = match_and_replace(self.pattern, self.repl, el.text)
                if new_value is not None:
                    el.text = new_value

    def __str__(self):
        return f"Replace all variables matching `{self.pattern}` with `{self.repl}` (XPath(s): `{self.xpaths}`)"


class MoveElements(XPathOperation):
    """
    Moves the matched xml elements inside the specified destination element.

    Examples::
        MoveElements("//xpath[contains(@expr, \"class='col-md-12'\")]/div", "/data")

    :param xpaths: an xpath `str` or an iterable of xpaths.
        See :class:`XPathOperation` for additional info about this argument.
    :param destination: an xpath of the destination element which to move
        the matched ones to. It must match only one element, will raise an error.
    :param prune_parents: if True (default), removes the ancestor elements that
        are left empty after moving the matched elements.
    """

    def __init__(self, xpaths, destination, prune_parents=True):
        super().__init__(xpaths)
        [self.destination] = list(self.generate_xpaths([destination]))
        self.prune_parents = prune_parents

    def _prune_empty(self, element):
        """Remove the given element if empty and recursively its ancestors"""
        parent_el = element.getparent()
        is_root = parent_el is None or not len(parent_el)
        element_is_empty = not len(element) and not element.text.strip()
        if not is_root and element_is_empty:
            parent_el.remove(element)
            self._prune_empty(parent_el)

    def __call__(self, arch, cr=None):
        [dest_el] = self.destination(arch)
        for el in self.get_elements(arch):
            parent_el = el.getparent()
            dest_el.append(el)
            if self.prune_parents:
                self._prune_empty(parent_el)

    def __str__(self):
        return f"Move elements matching XPath(s) `{self.xpaths}` to `{self.destination}`"


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
