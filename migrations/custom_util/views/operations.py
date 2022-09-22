"""
Classes that define and implement operations that can be performed on xml views elements.
"""
import enum
import logging
from abc import ABC, abstractmethod
from typing import Pattern

from lxml import etree

from .misc import extract_elements, get_arch


__all__ = [
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
]


_logger = logging.getLogger(__name__)


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


class XPathOperation(ViewOperation, ABC):  # noqa: B024
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
            assert isinstance(position, str)
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
        self.filename = filename
        with open(self.filename, "rb") as fp:
            arch = etree.fromstring(fp.read())
        elements_xml = extract_elements(arch, source_xpaths, view_name=self.filename)
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
                    try:
                        del el.attrib[attr_name]
                    except KeyError:
                        _logger.warning(f"No attribute '{attr_name}' was available to delete")
                else:
                    el.attrib[attr_name] = new_value

    def __str__(self):
        return f"Update attributes: `{self.attrs_dict}` (XPath(s): `{self.xpaths}`)"


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


# TODO: add some convenience operation to fixup xpath expr.
#       Very common case in studio views where the referenced fields are invalid.
