"""
Misc collection of helper classes and functions to fetch and fix views.
"""

import enum
from collections import defaultdict
from typing import Collection, Sequence

from lxml import etree

from odoo.upgrade import util

from ..helpers import get_ids


__all__ = [
    "WebsiteId",
    "ViewKey",
    "get_views_ids",
    "create_cow_views",
    "create_cow_view",
    "get_arch",
    "extract_elements",
    "extract_elements_from_view",
    "indent_tree",
]


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
            vk: {id_ for id_, (view_key, website_id) in res_by_id.items() if vk.matches(view_key, website_id)}
            for vk in keys
        }

        unmatched_ids = res_by_id.keys() - {id_ for ids in ids_by_key.values() for id_ in ids}
        assert not unmatched_ids, f"Query returned ids that don't match any {cls_name}: {unmatched_ids}"

        keys_by_website = {
            website_id: {vk for vk, ids in ids_by_key.items() if id_ in ids}
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
        return f"{self.__class__.__name__}({self.key!r}, {self.website_id!r})"


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
                    if view_key.website_id in {WebsiteId.NOTNULL, WebsiteId.NOTSET}:
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
