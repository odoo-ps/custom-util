import logging
import re
from typing import Collection

__all__ = [
    "STUDIO_XMLID_RE",
    "get_ids",
]

_logger = logging.getLogger(__name__)

STUDIO_XMLID_RE = re.compile(r"^(odoo_studio_).+$")
"""A compiled regex that matches xmlid names generated by Studio (except the module part)"""

def get_ids(cr, ids_or_xmlids=None, *more_ids_or_xmlids, model, ids=None, xmlids=None):
    """
    Get record ids from the given arguments.

    The function accepts xmlids and ids arguments in a variety of ways; these can be
    freely mixed and will be merged together for the final returned result.

    :param cr: the database cursor.
    :param ids_or_xmlids: an id as `int`, xmlid as `str`, or a collection of these.
    :param more_ids_or_xmlids: more ids or xmlids provided as positional arguments.
    :param model: a required keyword-only argument specifying the model for which to
        retrieve the ids from the provided xmlids.
        N.B. currently there's no check to ensure provided ids (as `int`s) actually
        match any valid record of the model.
    :param ids: a id or collection of ids as `int`s. Will be returned together
        with other fetched ids.
    :param xmlids: an xmlid or collection of xmlids as `str`s, whose ids will be fetched.
    :return: a set of `int` ids from the specified arguments.
    :raise ValueError: if one or more of the provided arguments are invalid ids/xmlids.
    :raise AttributeError: if no ids/xmlids are provided.
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
        raise AttributeError("No views ids or xmlids provided")

    if xmlids:
        # basic sanity check + fill-in missing module names for xmlids matching studio
        for xmlid in list(xmlids):
            if "." not in xmlid and STUDIO_XMLID_RE.match(xmlid):
                xmlids.discard(xmlid)
                xmlids.add(f"studio_customization.{xmlid}")
            elif not len(xmlid.split(".")) == 2:
                raise ValueError(
                    f'xmlid must be in the "<module>.<name>" format, got: {xmlid}'
                )

        cr.execute(
            "SELECT res_id FROM ir_model_data WHERE model = %s AND (module, name) IN %s",
            (
                model,
                tuple(tuple(xmlid.split(".")) for xmlid in xmlids),
            ),
        )
        ids |= {row[0] for row in cr.fetchall()}

    return ids
