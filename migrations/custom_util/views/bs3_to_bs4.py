#!/usr/bin/env python3
"""
Python script and module to convert a XML/HTML document with Bootstrap v3 to v4.
Can be imported as a module, or run as a standalone script, providing the path of the file to convert inplace.
Requires ``lxml`` as external dependency to be installed.
"""
import os.path
import re
import sys
from contextlib import contextmanager

import lxml.etree as etree


# TODO: also handle qweb-specific t-att(f)- attributes?


def _xpath_hasclass(context, *cls):
    """Checks if the context node has all the classes passed as arguments"""
    node_classes = set(context.context_node.attrib.get("class", "").split())
    return node_classes.issuperset(cls)


xpath_utils = etree.FunctionNamespace(None)
xpath_utils["hasclass"] = _xpath_hasclass


def innerxml(element, is_html=False):
    """
    Returns the inner XML of an element as a string.

    :param element: the element to convert.
    :type element: etree.ElementBase
    :param is_html: whether to use HTML for serialization, XML otherwise. Defaults to False.
    :type is_html: bool
    :rtype: str
    """
    return (element.text or "") + "".join(
        etree.tostring(child, encoding=str, method="html" if is_html else None) for child in element
    )


def split_classes(class_attr):
    """Returns a list of classes given a string of classes separated by spaces"""
    return (class_attr or "").split(" ")


def get_classes(element):
    """Returns the list of classes from the ``class`` attribute of an element"""
    return split_classes(element.get("class", ""))


def join_classes(classes):
    """Returns a string of classes given a list of classes"""
    return " ".join(classes)


def set_classes(element, classes):
    """Sets the ``class`` attribute of an element from a list of classes"""
    element.attrib["class"] = join_classes(classes)


@contextmanager
def edit_classes(element):
    """
    Context manager that allows to edit the classes of an element.
    It yields the classes as a list of strings and that list can be mutated to change the classes on the element.

    :param element: the element to edit the classes of.
    :type element: etree.ElementBase
    :return: a context manager that yields the list of classes.
    :rtype: list[str]
    """
    classes = get_classes(element)
    yield classes
    set_classes(element, classes)


ALL = object()


def simple_css_selector_to_xpath(selector):
    """
    Converts a basic CSS selector cases to an XPath expression.
    Supports node names, classes, ``>`` and ``,`` combinators.

    :param selector: the CSS selector to convert.
    :type selector: str
    :return: the resulting XPath expression.
    :rtype: str
    """
    separator = "//"
    xpath_parts = []
    combinators = "+>,~ "
    for selector_part in re.split(rf"(\s*[{combinators}]\s*)", selector):
        selector_part = selector_part.strip()

        if not selector_part:
            separator = "//"
        elif selector_part == ">":
            separator = "/"
        elif selector_part == ",":
            separator = "|"
        elif re.search(r"^(?:[a-z](-?\w+)*|[*.])", selector_part, flags=re.I):
            element, *classes = selector_part.split(".")
            if not element:
                element = "*"
            class_predicates = [f"[hasclass('{classname}')]" for classname in classes if classname]
            xpath_parts += [separator, element + "".join(class_predicates)]
        else:
            raise NotImplementedError(f"Unsupported CSS selector syntax: {selector}")

    return "".join(xpath_parts)


C = simple_css_selector_to_xpath


class ElementOperation:
    """
    Abstract base class for defining operations to be applied on etree elements.
    """

    def __call__(self, element, converter):
        """
        Performs the operation on the given element.

        Abstract method that must be implemented by subclasses.

        :param element: the etree element to apply the operation on.
        :type element: etree.ElementBase
        :param converter: the converter that's operating on the etree document.
        :type converter: BS3ToBS4Converter
        :return: the converted element, which could be the same provided, a different one, or None.
            The returned element should be used by the converter to chain further operations.
        :rtype: etree.ElementBase | None
        """
        raise NotImplementedError

    def on(self, element, converter):
        """Alias for __call__"""
        return self(element, converter)


class AddClass(ElementOperation):
    """
    Adds a class to an element.
    """

    def __init__(self, classname):
        self.classname = classname

    def __call__(self, element, converter):
        with edit_classes(element) as classes:
            if self.classname not in classes:
                classes.append(self.classname)
        return element


class RemoveClass(ElementOperation):
    """
    Removes a class from an element.
    """

    def __init__(self, classname):
        self.classname = classname

    def __call__(self, element, converter):
        with edit_classes(element) as classes:
            if self.classname in classes:
                classes.remove(self.classname)
        return element


class PullUp(ElementOperation):
    """
    Pulls up an element children to the parent element, removing the original element.
    """

    def __call__(self, element, converter):
        parent = element.getparent()
        if parent is None:
            raise ValueError(f"Cannot pull up contents of xml element with no parent: {element}")

        prev_sibling = element.getprevious()
        if prev_sibling is not None:
            prev_sibling.tail = ((prev_sibling.tail or "") + (element.text or "")) or None
        else:
            parent.text = ((parent.text or "") + (element.text or "")) or None

        for child in element:
            element.addprevious(child)

        parent.remove(element)
        return None


class ConvertBlockquote(ElementOperation):
    """
    Converts a BS3 ``<blockquote>`` element to a BS4 ``<div>`` element with the ``blockquote`` class.
    """

    def __call__(self, element, converter):
        blockquote = converter.copy_element(element, tag="div", add_classes="blockquote", copy_attrs=False)
        element.addnext(blockquote)
        element.getparent().remove(element)
        return blockquote


# TODO: consider merging MakeCard and ConvertCard into one operation class
class MakeCard(ElementOperation):
    """
    Pre-processes a BS3 panel, thumbnail, or well element to be converted to a BS4 card.
    Card components conversion is then handled by the ``ConvertCard`` operation class.
    """

    def __call__(self, element, converter):
        card = converter.element_factory("<div class='card'/>")
        card_body = converter.copy_element(
            element, tag="div", add_classes="card-body", remove_classes=ALL, copy_attrs=False
        )
        card.append(card_body)
        element.addnext(card)
        element.getparent().remove(element)
        return card


class ConvertCard(ElementOperation):
    """
    Fully converts a BS3 panel, thumbnail, or well element and their contents to a BS4 card.
    """

    POST_CONVERSIONS = {
        "title": ["card-title"],
        "description": ["card-description"],
        "category": ["card-category"],
        "panel-danger": ["card", "bg-danger", "text-white"],
        "panel-warning": ["card", "bg-warning"],
        "panel-info": ["card", "bg-info", "text-white"],
        "panel-success": ["card", "bg-success", "text-white"],
        "panel-primary": ["card", "bg-primary", "text-white"],
        "panel-footer": ["card-footer"],
        "panel-body": ["card-body"],
        "panel-title": ["card-title"],
        "panel-heading": ["card-header"],
        "panel-default": [],
        "panel": ["card"],
    }

    def _convert_child(self, child, old_card, new_card, converter):
        old_card_classes = get_classes(old_card)

        classes = get_classes(child)

        if "header" in classes or ("image" in classes and len(child)):
            add_classes = "card-header"
            remove_classes = ["header", "image"]
        elif "content" in classes:
            if "card-background" in old_card_classes:
                add_classes = "card-img-overlay"
            else:
                add_classes = "card-body"
            remove_classes = "content"
        elif {"card-footer", "footer", "text-center"} & set(classes):
            add_classes = "card-footer"
            remove_classes = "footer"
        else:
            new_card.append(child)
            return

        new_child = converter.copy_element(
            child, "div", add_classes=add_classes, remove_classes=remove_classes, copy_attrs=True
        )

        if "image" in classes:
            [img_el] = new_child.xpath("./img")[:1] or [None]
            if img_el is not None and "src" in img_el:
                new_child.attrib["style"] = (
                    f'background-image: url("{img_el.attrib["src"]}"); '
                    "background-position: center center; "
                    "background-size: cover;"
                )
                new_child.remove(img_el)

        new_card.append(new_child)

        if "content" in classes:  # TODO: consider skipping for .card-background
            [footer] = new_child.xpath("./*[hasclass('footer')]")[:1] or [None]
            if footer is not None:
                self._convert_child(footer, old_card, new_card, converter)
                new_child.remove(footer)

    def _postprocess(self, new_card):
        for old_class, new_classes in self.POST_CONVERSIONS.items():
            for element in new_card.xpath(f"(.|.//*)[hasclass('{old_class}')]"):
                with edit_classes(element) as classes:
                    if old_class in classes:
                        classes.remove(old_class)
                    for new_class in new_classes:
                        if new_class not in classes:
                            classes.append(new_class)

    def __call__(self, element, converter):
        classes = get_classes(element)
        new_card = converter.copy_element(element, tag="div", copy_attrs=True, copy_contents=False)
        wrapper = new_card
        if "card-horizontal" in classes:
            wrapper = etree.SubElement(new_card, "div", {"class": "row"})

        for child in element:
            self._convert_child(child, element, wrapper, converter)

        self._postprocess(new_card)
        element.addnext(new_card)
        element.getparent().remove(element)
        return new_card


INPUTS_CONVERSIONS = {
    C(".form-group .control-label"): [AddClass("form-control-label"), RemoveClass("control-label")],
    C(".form-group .text-help"): [AddClass("form-control-feedback"), RemoveClass("text-help")],
    C(".control-group .help-block"): [AddClass("form-text"), RemoveClass("help-block")],
    C(".form-group-sm"): [AddClass("form-control-sm"), RemoveClass("form-group-sm")],
    C(".form-group-lg"): [AddClass("form-control-lg"), RemoveClass("form-group-lg")],
    C(".form-control.input-lg"): [AddClass("form-control-lg"), RemoveClass("input-lg")],
    C(".form-control.input-sm"): [AddClass("form-control-sm"), RemoveClass("input-sm")],
}
HIDE_CONVERSIONS = {
    C(".hidden-xs"): [AddClass("d-none"), RemoveClass("hidden-xs")],
    C(".hidden-sm"): [AddClass("d-sm-none"), RemoveClass("hidden-sm")],
    C(".hidden-md"): [AddClass("d-md-none"), RemoveClass("hidden-md")],
    C(".hidden-lg"): [AddClass("d-lg-none"), RemoveClass("hidden-lg")],
    C(".visible-xs"): [AddClass("d-block"), AddClass("d-sm-none"), RemoveClass("visible-xs")],
    C(".visible-sm"): [AddClass("d-block"), AddClass("d-md-none"), RemoveClass("visible-sm")],
    C(".visible-md"): [AddClass("d-block"), AddClass("d-lg-none"), RemoveClass("visible-md")],
    C(".visible-lg"): [AddClass("d-block"), AddClass("d-xl-none"), RemoveClass("visible-lg")],
}
IMAGE_CONVERSIONS = {
    C(".img-rounded"): [AddClass("rounded"), RemoveClass("img-rounded")],
    C(".img-circle"): [AddClass("rounded-circle"), RemoveClass("img-circle")],
    C(".img-responsive"): [AddClass("img-fluid"), AddClass("d-block"), RemoveClass("img-responsive")],
}
BUTTONS_CONVERSIONS = {
    C(".btn-default"): [AddClass("btn-secondary"), RemoveClass("btn-default")],
    C(".btn-xs"): [AddClass("btn-sm"), RemoveClass("btn-xs")],
    C(".btn-group.btn-group-xs"): [AddClass("btn-group-sm"), RemoveClass("btn-group-xs")],
    C(".dropdown .divider"): [AddClass("dropdown-divider"), RemoveClass("divider")],
    C(".badge"): [AddClass("badge"), AddClass("badge-pill")],
    C(".label"): [AddClass("badge"), RemoveClass("label")],
    C(".label-default"): [AddClass("badge-secondary"), RemoveClass("label-default")],
    C(".label-primary"): [AddClass("badge-primary"), RemoveClass("label-primary")],
    C(".label-success"): [AddClass("badge-success"), RemoveClass("label-success")],
    C(".label-info"): [AddClass("badge-info"), RemoveClass("label-info")],
    C(".label-warning"): [AddClass("badge-warning"), RemoveClass("label-warning")],
    C(".label-danger"): [AddClass("badge-danger"), RemoveClass("label-danger")],
    C(".breadcrumb > li"): [AddClass("breadcrumb-item"), RemoveClass("breadcrumb")],
}
LI_CONVERSIONS = {
    C(".list-inline > li"): [AddClass("list-inline-item")],
}
PAGINATION_CONVERSIONS = {
    C(".pagination > li"): [AddClass("page-item")],
    C(".pagination > li > a"): [AddClass("page-link")],
}
CAROUSEL_CONVERSIONS = {
    C(".carousel .carousel-inner > .item"): [AddClass("carousel-item"), RemoveClass("item")],
}
PULL_CONVERSIONS = {
    C(".pull-right"): [AddClass("float-right"), RemoveClass("pull-right")],
    C(".pull-left"): [AddClass("float-left"), RemoveClass("pull-left")],
    C(".center-block"): [AddClass("mx-auto"), RemoveClass("center-block")],
}
WELL_CONVERSIONS = {
    C(".well"): [MakeCard()],
    C(".thumbnail"): [MakeCard()],
}
BLOCKQUOTE_CONVERSIONS = {
    C("blockquote"): [ConvertBlockquote()],
    C(".blockquote.blockquote-reverse"): [AddClass("text-right"), RemoveClass("blockquote-reverse")],
}
DROPDOWN_CONVERSIONS = {
    C(".dropdown-menu > li > a"): [AddClass("dropdown-item")],
    C(".dropdown-menu > li"): [PullUp()],
}
IN_CONVERSIONS = {
    C(".in"): [AddClass("show"), RemoveClass("in")],
}
TABLE_CONVERSIONS = {
    C("tr.active, td.active"): [AddClass("table-active"), RemoveClass("active")],
    C("tr.success, td.success"): [AddClass("table-success"), RemoveClass("success")],
    C("tr.info, td.info"): [AddClass("table-info"), RemoveClass("info")],
    C("tr.warning, td.warning"): [AddClass("table-warning"), RemoveClass("warning")],
    C("tr.danger, td.danger"): [AddClass("table-danger"), RemoveClass("danger")],
    C("table.table-condesed"): [AddClass("table-sm"), RemoveClass("table-condesed")],
}
NAVBAR_CONVERSIONS = {
    C(".nav.navbar > li > a"): [AddClass("nav-link")],
    C(".nav.navbar > li"): [AddClass("nav-intem")],
    C(".navbar-btn"): [AddClass("nav-item"), RemoveClass(".navbar-btn")],
    C(".navbar-nav"): [AddClass("ml-auto"), RemoveClass("navbar-right"), RemoveClass("nav")],
    C(".navbar-toggler-right"): [AddClass("ml-auto"), RemoveClass("navbar-toggler-right")],
    C(".navbar-nav > li > a"): [AddClass("nav-link")],
    C(".navbar-nav > li"): [AddClass("nav-item")],
    C(".navbar-nav > a"): [AddClass("navbar-brand")],
    C(".navbar-fixed-top"): [AddClass("fixed-top"), RemoveClass("navbar-fixed-top")],
    C(".navbar-toggle"): [AddClass("navbar-toggler"), RemoveClass("navbar-toggle")],
    C(".nav-stacked"): [AddClass("flex-column"), RemoveClass("nav-stacked")],
    C("nav.navbar"): [AddClass("navbar-expand-lg")],
    C("button.navbar-toggle"): [AddClass("navbar-expand-md"), RemoveClass("navbar-toggle")],
}
CARD_CONVERSIONS = {
    C(".panel"): [ConvertCard()],
    C(".card"): [ConvertCard()],
}
# TODO: grid offsets: col-(\w+)-offset-(\d+) -> offset-$1-$2

CONVERSIONS = [
    # priority 3
    INPUTS_CONVERSIONS,
    HIDE_CONVERSIONS,
    IMAGE_CONVERSIONS,
    BUTTONS_CONVERSIONS,
    LI_CONVERSIONS,
    # priority 2
    PAGINATION_CONVERSIONS,
    CAROUSEL_CONVERSIONS,
    PULL_CONVERSIONS,
    WELL_CONVERSIONS,
    BLOCKQUOTE_CONVERSIONS,
    DROPDOWN_CONVERSIONS,
    # priority 1
    IN_CONVERSIONS,
    TABLE_CONVERSIONS,
    # navbar
    NAVBAR_CONVERSIONS,
    # card
    CARD_CONVERSIONS,
]


class BS3to4Converter:
    """
    Class for converting XML or HTML strings or code from Bootstrap v3 to v4.

    :param tree: the parsed XML or HTML tree to convert.
    :type tree: etree.ElementTree
    :param is_html: whether the tree is an HTML document.
    :type is_html: bool
    """

    def __init__(self, tree, is_html=False):
        self.tree = tree
        self.is_html = is_html

    def convert(self):
        """Converts the loaded document, and returns the converted document."""
        for conversions_group in CONVERSIONS:
            for xpath, operations in conversions_group.items():
                for element in self.tree.xpath(xpath):
                    for operation in operations:
                        if element is None:  # previous operations that returned None (ie. deleted element)
                            raise ValueError("Matched xml element is not available anymore! Check operations.")
                        element = operation.on(element, self)
        return self.tree

    @classmethod
    def convert_arch(cls, arch, is_html=False):
        """
        Class method for converting a string of XML or HTML code.

        :param arch: the XML or HTML code to convert.
        :type arch: str
        :param is_html: whether the arch is an HTML document.
        :type is_html: bool
        :return: the converted XML or HTML code.
        :rtype: str
        """
        arch = f"<data>{arch}</data>"
        tree = etree.fromstring(arch, parser=etree.HTMLParser() if is_html else None)
        tree = cls(tree, is_html).convert()
        return "\n".join(
            etree.tostring(child, encoding="unicode", with_tail=True, method="html" if is_html else None)
            for child in tree
        )

    @classmethod
    def convert_file(cls, path):
        """
        Class method for converting a XML or HTML file inplace.

        :param path: the path to the XML or HTML file to convert.
        :type path: str
        :rtpe: None
        """
        is_html = os.path.splitext(path)[1].startswith("htm")
        tree = etree.parse(path, parser=etree.HTMLParser() if is_html else None)
        tree = cls(tree, is_html).convert()
        tree.write(path, encoding="utf-8", method="html" if is_html else None, xml_declaration=not is_html)

    def element_factory(self, *args, **kwargs):
        """
        Helper method to be used by operation for creating new elements using the correct document type.
        Basically a wrapper for either etree.XML or etree.HTML depending on the type of document loaded.

        :param args: positional arguments to pass to the etree.XML or etree.HTML function.
        :param kwargs: keyword arguments to pass to the etree.XML or etree.HTML function.
        :return: the created element.
        """
        return etree.HTML(*args, **kwargs) if self.is_html else etree.XML(*args, **kwargs)

    def build_element(self, tag, classes=None, contents=None, **attributes):
        """
        Helper method to create a new element with the given tag, classes, contents and attributes.
        Like :meth:`~.element_factory`, can be used by operations to create elements abstracting away the document type.

        :param tag: the tag of the element to create.
        :type tag: str
        :param classes: the classes to set on the new element.
        :type classes: typing.Iterable[str] | None
        :param contents: the contents of the new element (ie. inner text/HTML/XML).
        :type contents: str | None
        :param attributes: attributes to set on the new element, provided as keyword arguments.
        :type attributes: str
        :return: the created element.
        :rtype: etree.ElementBase
        """
        element = self.element_factory(f"<{tag}>{contents or ''}</{tag}>")
        if classes:
            set_classes(element, classes)
        for name, value in attributes.items():
            element.attrib[name] = value
        return element

    def copy_element(
        self,
        element,
        tag=None,
        add_classes=None,
        remove_classes=None,
        copy_attrs=True,
        copy_contents=True,
        **attributes,
    ):
        """
        Helper method that creates a copy of an element, optionally changing the tag, classes, contents and attributes.
        Like :meth:`~.element_factory`, can be used by operations to copy elements abstracting away the document type.

        :param element: the element to copy.
        :type element: etree.ElementBase
        :param tag: if specified, overrides the tag of the new element.
        :type tag: str | None
        :param add_classes: if specified, adds the given class(es) to the new element.
        :type add_classes: str | typing.Iterable[str] | None
        :param remove_classes: if specified, removes the given class(es) from the new element.
        :type remove_classes: str | typing.Iterable[str] | None
        :param copy_attrs: if True, copies the attributes of the source element to the new one. Defaults to True.
        :type copy_attrs: bool
        :param copy_contents: if True, copies the contents of the source element to the new one. Defaults to True.
        :type copy_contents: bool
        :param attributes: attributes to set on the new element, provided as keyword arguments.
            Will be merged with the attributes of the source element, overriding the latter.
        :type attributes: str
        :return: the new copied element.
        :rtype: etree.ElementBase
        """
        tag = tag or element.tag

        if remove_classes is ALL:
            classes = []
            remove_classes = None
        else:
            classes = get_classes(element)

        if isinstance(add_classes, str):
            add_classes = [add_classes]
        for classname in add_classes or []:
            if classname not in classes:
                classes.append(classname)

        if isinstance(remove_classes, str):
            remove_classes = [remove_classes]
        for classname in remove_classes or []:
            if classname in classes:
                classes.remove(classname)

        contents = innerxml(element, is_html=self.is_html) if copy_contents else None

        if copy_attrs:
            attributes.update(element.attrib)

        return self.build_element(tag, classes=classes, contents=contents, **attributes)


def convert_tree(tree, is_html=False):
    """
    Converts an already parsed lxml tree from Bootstrap v3 to v4 inplace.

    :param tree: the lxml tree to convert.
    :type tree: etree.ElementTree
    :param is_html: whether the tree is an HTML document.
    :type is_html: bool
    :return: the converted lxml tree.
    :rtype: etree.ElementTree
    """
    return BS3to4Converter(tree, is_html).convert()


convert_arch = BS3to4Converter.convert_arch
convert_file = BS3to4Converter.convert_file


if __name__ == "__main__":
    convert_file(sys.argv[1])
