import re
from contextlib import contextmanager

import lxml.etree as etree


def innerxml(element):
    return (element.text or "") + "".join(etree.tostring(child, encoding=str) for child in element)


def split_classes(class_attr):
    return (class_attr or "").split(" ")


def get_classes(element):
    return split_classes(element.get("class", ""))


def join_classes(classes):
    return " ".join(classes)


def set_classes(element, classes):
    element.attrib["class"] = join_classes(classes)


@contextmanager
def edit_classes(element):
    classes = get_classes(element)
    yield classes
    set_classes(element, classes)


def build_element(tag, classes=None, contents=None, **attributes):
    element = etree.XML(f"<{tag}>{contents or ''}</{tag}>")
    if classes:
        set_classes(element, classes)
    for name, value in attributes.items():
        element.attrib[name] = value
    return element


ALL = object()


def copy_element(element, tag=None, add_classes=None, remove_classes=None, copy_attrs=True, **attributes):
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

    contents = innerxml(element)

    if copy_attrs:
        attributes.update(element.attrib)

    return build_element(tag, classes=classes, contents=contents, **attributes)


def simple_css_selector_to_xpath(selector):
    separator = "//"
    xpath_parts = []
    for selector_part in re.split(r"(\s+|\s*[+>]\s*)", selector):
        selector_part = selector_part.strip()

        if not selector_part:
            separator = "//"
        elif selector_part == ">":
            separator = "/"
        else:
            element, *classes = selector_part.split(".")
            if not element:
                element = "*"
            class_predicates = [f"[contains(@class, '{classname}')]" for classname in classes if classname]
            xpath_parts += [separator, element + "".join(class_predicates)]

    return "".join(xpath_parts)


C = simple_css_selector_to_xpath


class XmlOperation:
    def __call__(self, element):
        raise NotImplementedError

    def on(self, element):
        return self(element)


class AddClass(XmlOperation):
    def __init__(self, classname):
        self.classname = classname

    def __call__(self, element):
        with edit_classes(element) as classes:
            if self.classname not in classes:
                classes.append(self.classname)
        return element


class RemoveClass(XmlOperation):
    def __init__(self, classname):
        self.classname = classname

    def __call__(self, element):
        with edit_classes(element) as classes:
            if self.classname in classes:
                classes.remove(self.classname)
        return element


class PullUp(XmlOperation):
    def __call__(self, element):
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


class ConvertBlockquote(XmlOperation):
    def __call__(self, element):
        blockquote = copy_element(element, tag="div", add_classes="blockquote", copy_attrs=False)
        element.addnext(blockquote)
        element.getparent().remove(element)
        return blockquote


class MakeCard(XmlOperation):
    def __call__(self, element):
        card = etree.XML("<div class='card'/>")
        card_body = copy_element(element, tag="div", add_classes="card-body", remove_classes=ALL, copy_attrs=False)
        card.append(card_body)
        element.addnext(card)
        element.getparent().remove(element)
        return card


class ConvertCard(XmlOperation):
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
        "panel": ["card"],
    }

    def _convert_child(self, child, old_card, new_card):
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
            return  # TODO: just add instead?

        new_child = copy_element(child, "div", add_classes=add_classes, remove_classes=remove_classes, copy_attrs=False)

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
            [footer] = new_child.xpath("./*[contains(@class, 'footer')]")[:1] or [None]
            if footer is not None:
                self._convert_child(footer, old_card, new_card)
                new_child.remove(footer)

    def _postprocess(self, new_card):
        for old_class, new_classes in self.POST_CONVERSIONS.items():
            for element in new_card.xpath(f".//*[contains(@class, '{old_class}')]"):
                with edit_classes(element) as classes:
                    if old_class in classes:
                        classes.remove(old_class)
                    for new_class in new_classes:
                        if new_class not in classes:
                            classes.append(new_class)

    def __call__(self, element):
        classes = get_classes(element)
        new_card = copy_element(element, tag="div", copy_attrs=False)
        wrapper = new_card
        if "card-horizontal" in classes:
            wrapper = etree.SubElement(new_card, "div", {"class": "row"})
        for child in element:
            self._convert_child(child, element, wrapper)
        self._postprocess(element)
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
    C(".img-responsive"): [AddClass("img-fluid"), RemoveClass("img-responsive")],
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
LI_CONVERSIONS = {C(".list-inline > li"): [AddClass("list-inline-item")]}
PAGINATION_CONVERSIONS = {
    C(".pagination > li"): [AddClass("page-item")],
    C(".pagination > li > a"): [AddClass("page-link")],
}
CAROUSEL_CONVERSIONS = {C(".carousel .carousel-inner > .item"): [AddClass("carousel-item"), RemoveClass("item")]}
PULL_CONVERSIONS = {
    C(".pull-right"): [AddClass("float-right"), RemoveClass("pull-right")],
    C(".pull-left"): [AddClass("float-left"), RemoveClass("pull-left")],
    C(".center-block"): [AddClass("mx-auto"), RemoveClass("center-block")],
}
WELL_CONVERSIONS = {C(".well"): [MakeCard()], C(".thumbnail"): [MakeCard()]}
BLOCKQUOTE_CONVERSIONS = {
    C("blockquote"): [ConvertBlockquote()],
    C(".blockquote.blockquote-reverse"): [AddClass("text-right"), RemoveClass("blockquote-reverse")],
}
DROPDOWN_CONVERSIONS = {C(".dropdown-menu > li > a"): [AddClass("dropdown-item")], C(".dropdown-menu > li"): [PullUp()]}
IN_CONVERSIONS = {C(".in"): [AddClass("show"), RemoveClass("in")]}
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
CARD_CONVERSIONS = {C(".card"): [ConvertCard()]}

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


def convert_xml(xml):
    for conversions_group in CONVERSIONS:
        for xpath, operations in conversions_group.items():
            for element in xml.xpath(xpath):
                for operation in operations:
                    if element is None:  # previous operations that returned None (ie. deleted element)
                        raise ValueError("Matched xml element is not available anymore! Check operations.")
                    element = operation.on(element)
