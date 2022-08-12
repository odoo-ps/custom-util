import logging
import re
from collections import defaultdict
from typing import Collection

from odoo.upgrade import util


__all__ = [
    "STUDIO_XMLID_RE",
    "expand_studio_xmlids",
    "get_ids",
    "get_model_xmlid_basename",
]


_logger = logging.getLogger(__name__)


STUDIO_XMLID_RE = re.compile(r"^(odoo_studio_).+$")
"""A compiled regex that matches xmlid names generated by Studio (except the module part)"""


def expand_studio_xmlids(xmlids, do_raise=True):
    """
    Expand studio xmlids names into full xmlids (prepending the ``studio_customization.`` module part).

    :param xmlids: an xmlid name or iterable of names.
    :type xmlids: str | typing.Iterable[str]
    :param do_raise: Raise if any xmlid is not a studio xmlid, or fails to be detected as such.
        Otherwise the failing xmlid will be returned as-is. Defaults to True.
    :type do_raise: bool
    :raise ValueError: if ``do_raise`` is True and any xmlid fails to be detected as studio xmlid.
    :return: a list of expanded xmlids.
    :rtype: list[str]
    """

    def expand(xmlid):
        if STUDIO_XMLID_RE.match(xmlid):
            return f"studio_customization.{xmlid}"
        elif xmlid.startswith("studio_customization.") or not do_raise:
            return xmlid
        raise ValueError(f"Failed detecting studio xmlid: {xmlid}")

    if isinstance(xmlids, str):
        xmlids = [xmlids]
    return [expand(xmlid) for xmlid in xmlids]


def get_ids(
    cr, ids_or_xmlids=None, *more_ids_or_xmlids, model, ids=None, xmlids=None, ensure_exist=False, mapped=False
):
    """
    Get record ids from the given arguments.

    The function accepts xmlids and ids arguments in a variety of ways; these can be
    freely mixed and will be merged together for the final returned result.

    :param cr: the database cursor.
    :type cr: psycopg2.cursor
    :param ids_or_xmlids: an id as `int`, xmlid as `str`, or a collection of these.
    :type ids_or_xmlids: int | str | typing.Collection[int | str]
    :param more_ids_or_xmlids: more ids or xmlids provided as positional arguments.
    :type more_ids_or_xmlids: int | str
    :param model: a required keyword-only argument specifying the model for which to
        retrieve the ids from the provided xmlids.
        N.B. currently there's no check to ensure provided ids (as `int`s) actually
        match any valid record of the model.
    :type model: str
    :param ids: a id or collection of ids as `int`s. Will be returned together
        with other fetched ids.
    :type ids: int | typing.Collection[int]
    :param xmlids: an xmlid or collection of xmlids as `str`s, whose ids will be fetched.
    :type xmlids: str | typing.Collection[str]
    :param ensure_exist: if True, check that all the passed values match existing records in the database.
        Defaults to False.
    :type ensure_exist: bool
    :param mapped: if True, return a mapping of the passed values to matched ids, otherwise a set of all matched ids.
        N.B. expanded xmlids (eg. partial studio ones) will be returned twice, as source and expanded form.
        Defaults to False.
    :type mapped: bool
    :return: the matched records ids for the specified arguments, as dict or set, depending on the ``mapped`` argument.
    :rtype: typing.Set[int] | typing.MutableMapping[int | str, typing.Set[int]]
    :raise ValueError: if one or more of the provided arguments are invalid ids/xmlids.
    :raise TypeError: if no ids/xmlids are provided.
    :raise IndexError: if ``ensure_exist`` is enabled and one or more ids/xmlids don't exist in the database.
    """

    def ensure_set(value):
        """Makes sure the provided value is returned as a set"""
        if value is None:
            return set()
        if isinstance(value, (int, str)):  # check before, as str is also a Collection
            return {value}
        if isinstance(value, Collection):
            return set(value)
        raise ValueError(f'Invalid id/xmlid value type "{type(value)}": {value}')

    ids = ensure_set(ids)
    xmlids = ensure_set(xmlids)

    if ids_or_xmlids or more_ids_or_xmlids:
        ids_or_xmlids = ensure_set(ids_or_xmlids)
        ids_or_xmlids |= set(more_ids_or_xmlids)
        for i in ids_or_xmlids:
            if isinstance(i, int):
                ids.add(i)
            elif isinstance(i, str):
                xmlids.add(i)
            else:
                raise ValueError(f'Invalid id/xmlid value type "{type(i)}": {i}')

    if not (ids or xmlids):
        raise TypeError("No ids or xmlids provided")

    id_origins_map = {id_: {id_} for id_ in ids}  # id to set of origins (ie. ids, xmlids, etc.)

    if xmlids:
        xmlids_origins = {xmlid: xmlid for xmlid in xmlids}

        # basic sanity check + fill-in missing module names for xmlids matching studio
        for xmlid in list(xmlids):
            if "." not in xmlid:
                [studio_xmlid] = expand_studio_xmlids(xmlid, do_raise=False)
                if studio_xmlid != xmlid:
                    xmlids.discard(xmlid)
                    xmlids.add(studio_xmlid)
                    xmlids_origins[studio_xmlid] = xmlids_origins.pop(xmlid)
                    xmlid = studio_xmlid

            if not len(xmlid.split(".")) == 2:
                raise ValueError(f'xmlid must be in the "<module>.<name>" format, got: {xmlid}')

        cr.execute(
            "SELECT res_id, module||'.'||name FROM ir_model_data WHERE model = %s AND (module, name) IN %s",
            (model, tuple(tuple(xmlid.split(".")) for xmlid in xmlids)),
        )
        xmlids_to_ids = {xmlid: res_id for res_id, xmlid in cr.fetchall()}
        id_origins_map.update((res_id, {xmlid, xmlids_origins[xmlid]}) for xmlid, res_id in xmlids_to_ids.items())
        if ensure_exist:
            missing_xmlids = xmlids - xmlids_to_ids.keys()
            if missing_xmlids:
                id_origins_map[None] = missing_xmlids  # missing xmlids

    if ensure_exist:
        table = util.table_of_model(cr, model)
        cr.execute(f"SELECT array_agg(id) FROM {table} WHERE id IN %s", [tuple(id_origins_map.keys())])
        [[existing_ids]] = cr.fetchall()
        missing_ids = id_origins_map.keys() - set(existing_ids or [])
        if missing_ids:
            unmatched_origins = {origin for id_ in missing_ids | {None} for origin in id_origins_map[id_]}
            raise IndexError(f"`{model}` records for these ids/xmlids are missing in the database: {unmatched_origins}")

    if mapped:
        ids_by_origin = defaultdict(set)
        for id_, origins in id_origins_map.items():
            for origin in origins:
                ids_by_origin[origin].add(id_)
        return dict(ids_by_origin)
    else:
        return set(id_origins_map.keys())


def get_model_xmlid_basename(model_name):
    replaced = model_name.replace(".", "_")
    return f"model_{replaced}"
