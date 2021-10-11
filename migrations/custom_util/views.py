"""
Utility functions and classes to perform common migration operations on views
and other xml documents in a database.
"""
import enum
import logging
from abc import ABC, abstractmethod
from textwrap import dedent
from typing import Pattern

from lxml import etree

from odoo.upgrade import util

from .custom_util import get_ids


__all__ = [
    "get_views_ids",
    "edit_views",
    "activate_views",
    "indent_tree",
    "ViewOperation",
    "XPathOperation",
    "AddElementPosition",
    "AddElements",
    "RemoveElements",
    "RemoveFields",
    "AddInvisibleSiblingFields",
    "RenameElements",
    "UpdateAttributes",
    "ReplacePosition",
    "ReplaceValue",
]


_logger = logging.getLogger(__name__)


def get_views_ids(cr, ids_or_xmlids=None, *more_ids_or_xmlids, ids=None, xmlids=None):
    """
    Get views ids from the given arguments.

    The function accepts xmlids and ids arguments in a variety of ways; these can be
    freely mixed and will be merged together for the final returned result.

    :param cr: the database cursor.
    :param ids_or_xmlids: an id as `int`, xmlid as `str`, or a collection of these.
    :param more_ids_or_xmlids: more ids or xmlids provided as positional arguments.
    :param ids: a id or collection of ids as `int`s. Will be returned together
        with other fetched ids.
    :param xmlids: an xmlid or collection of xmlids as `str`s, whose ids will be fetched.
    :return: a set of `int` ids of views from the specified arguments.
    :raise ValueError: if one or more of the provided arguments are invalid ids/xmlids.
    :raise AttributeError: if no ids/xmlids are provided.
    """
    # TODO: add ability / special case to grab views by `key`
    return get_ids(
        cr,
        ids_or_xmlids,
        *more_ids_or_xmlids,
        model="ir.ui.view",
        ids=ids,
        xmlids=xmlids,
    )


def edit_views(cr, view_operations, verbose=True):
    """
    Utility function to edit one or more views with the specified operations.

    It accepts a mapping of operations to be executed on views, with the views
    identifiers (ids, xmlids) as keys and a sequence of operations as values.
    The operations must be instances of :class:`ViewOperation`.

    Since these are not bound to any specific view, but rather just define an action
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
        }
        edit_views(cr, view_operations)
    """
    for view_id_or_xmlid, operations in view_operations.items():
        if not operations:  # silently skip views with no operations
            continue
        [view_id] = get_views_ids(cr, view_id_or_xmlid)
        with util.edit_view(cr, view_id=view_id, skip_if_not_noupdate=False) as arch:
            _logger.log(
                logging.INFO if verbose else logging.DEBUG,
                f'Patching ir.ui.view "{view_id_or_xmlid}"',
            )
            for op in operations:
                _logger.debug(op)
                op(arch)
            indent_tree(arch)


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
        _logger.info(
            f"Tried to activate views that were already active: {ids-activated_views}"
        )
    _logger.debug(f"Activated views: {activated_views}")
    return activated_views


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
    def __call__(self, arch):
        """
        Abstract method with the actual implementation of the logic of the operation.

        :param arch: `lxml.etree` representing the architecture of the document.
        """

    def on(self, arch):
        """
        Many elegance, much wow: `ViewOperation.on(arch)`.
        Just a more semantically meaningful shortcut for :func:`__call__`.
        """
        return self(arch)

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
                raise ValueError(
                    f'XPath expression "{xpath}" does not yield elements. Got: {elements}'
                )
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
        self.elements_xml = dedent(elements_xml)
        if not isinstance(position, AddElementPosition):
            try:
                position = AddElementPosition[position.upper()]
            except KeyError as exc:
                raise ValueError(
                    f'"position" must be one of '
                    f'{",".join(e.name for e in AddElementPosition)}, got "{position}"'
                ) from exc
        self.position = position
        self.elements = self._prepare_elements(elements_xml)

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

    def __call__(self, arch):
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


class RemoveElements(XPathOperation):
    """
    Removes all elements from the view with matching xpaths.

    Keep in mind that the structure of the document is changed, which may affect
    other xpath selectors, so, usually, removing Elements should be the last operation.

    Examples::
        RemoveElements("//*")

        RemoveElements([f"//data/xpath[{i}]" for i in (12, 13, 14)])
    """

    def __call__(self, arch):
        for el in self.get_elements(arch):
            el.getparent().remove(el)


class RemoveFields(RemoveElements):
    """
    Removes all ``<field>`` elements in a view that match the given ``name``s.

    Examples::
        RemoveFields(["state", "id"])

    :param names: the elements names as a `str` or an iterable of `str`
    """

    def __init__(self, names):
        if isinstance(names, str):
            names = [names]
        self.xpaths = [f'//field[@name="{name}"]' for name in names]
        super().__init__(self.xpaths)


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
        super().__init__(
            f'//field[@name="{name}" and not(../field[@name="{sibling_name}"])]',
            f'<field name="{sibling_name}" invisible="1" />',
            position=position,
        )


class RenameElements(XPathOperation):
    """
    Renames the value of the attribute ``name`` for all matching elements in a view.
    Especially useful in case a field of a model was renamed.

    Examples::
        RenameElements("payment_term_id", "invoice_payment_term_id")

    :param name: a `str` of the current ``name`` to match.
    :param new_name: a `str` of the new replacement ``name``.
    """

    def __init__(self, name, new_name):
        super().__init__(f'//*[@name="{name}"]')
        self.name = name
        self.new_name = new_name

    def __call__(self, arch):
        for el in self.get_elements(arch):
            el.attrib["name"] = self.new_name


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

    def __call__(self, arch):
        for el in self.get_elements(arch):
            for attr_name, new_value in self.attrs_dict.items():
                if new_value is None:
                    del el.attrib[attr_name]
                else:
                    el.attrib[attr_name] = new_value


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

    def __init__(
        self, pattern, repl, xpaths="//*", position=ReplacePosition.ATTRIBUTES
    ):
        super().__init__(xpaths)
        self.pattern = pattern
        self.repl = repl
        if not isinstance(position, ReplacePosition):
            try:
                position = ReplacePosition[position.upper()]
            except KeyError as exc:
                raise ValueError(
                    f'"position" must be one of '
                    f'{",".join(e.name for e in ReplacePosition)}, got "{position}"'
                ) from exc
        self.position = position

    def __call__(self, arch):
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