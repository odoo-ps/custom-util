from typing import Dict, Iterable, Union

from lxml import etree
from lxml.builder import E

from odoo.upgrade import util

from .views import ViewOperation


__all__ = [
    "add_view_modifications_to_migration_reports",
]


def add_view_modifications_to_migration_reports(
    cr,
    view_xmlid_to_operations: Dict[str, Iterable[ViewOperation]],
    announce: bool = True,
):
    for view_xmlid, view_operations in view_xmlid_to_operations.items():
        operations_ul = _build_ulist(_escape_code_elements(str(view_operation)) for view_operation in view_operations)
        view_link_el = etree.fromstring(
            util.get_anchor_link_to_record("ir.ui.view", util.ref(cr, view_xmlid), view_xmlid)
        )

        view_details = E.p(view_link_el, E.br, _build_details(["Operations:"], operations_ul))
        util.add_to_migration_reports(
            etree.tostring(view_details, encoding="unicode"),
            category="Modified custom/studio views",
            format="html",
        )

    if announce:
        # TODO: add custom header when PR into upgrade repo is merged
        util.announce_migration_report(cr)


def _build_details(summary: Iterable[Union[etree.Element, str]], folded: etree.Element) -> etree.Element:
    return E.details(E.summary(*list(summary)), folded)


def _build_ulist(li_contents: Iterable[Union[etree.Element, str]]) -> etree.Element:
    return E.ul(*[E.li(content) for content in li_contents])


def _escape_code_elements(s: str) -> etree.Element:
    try:
        # wrapped in a <p>
        return etree.fromstring(util.md2html(s))
    except ImportError:
        # code is enclosed by '`' -> odd = code; even = normal text
        children_elements = [E.code(part) if i % 2 else part for i, part in enumerate(s.split("`"))]
        return E.p(*children_elements)
