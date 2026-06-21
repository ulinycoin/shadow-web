import re
from lxml import html, etree
from typing import Dict, List, Tuple, Any

from .grouping import apply_groups_to_actions, group_action_map

# Tags that are completely useless for LLMs
REMOVE_TAGS = {
    'script', 'style', 'noscript', 'iframe',
    'meta', 'link', 'head', 'canvas', 'audio', 'video'
}

# Attributes to keep for LLM context and functionality
KEEP_ATTRS = {
    'href', 'src', 'alt', 'type', 'value', 'placeholder',
    'name', 'for', 'action', 'method', 'aria-label',
    'data-sid', 'data-sw-bind',
}

# Interactive tag types
INTERACTIVE_TAGS = {'a', 'button', 'input', 'select', 'textarea'}

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def get_element_label(el) -> str:
    """Extracts a human-readable label for an interactive element."""
    # 1. Try aria-label (highest priority for screen readers and LLMs)
    label = el.get('aria-label')
    if label:
        return clean_text(label)
        
    # 2. Try placeholder (for inputs)
    label = el.get('placeholder')
    if label:
        return clean_text(label)
        
    # 3. Try inner text
    label = clean_text(el.text_content())
    if label:
        return label
        
    # 4. Try name or alt attributes
    for attr in ('name', 'alt', 'value'):
        label = el.get(attr)
        if label:
            return clean_text(label)
            
    return ""

def process_html(html_text: str) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Parses HTML, strips styles/scripts, adds shadow IDs (data-sid) to interactive
    elements, extracts an Action Map, and builds semantic groups.

    Returns:
        Tuple of (clean_html_string, action_map_list, groups_list)
    """
    tree = html.fromstring(html_text)
    
    # 1. Remove non-content tags
    for tag in REMOVE_TAGS:
        for el in tree.xpath(f'//{tag}'):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
                
    # 2. Identify interactive elements, inject data-sid and build action map
    action_map = []
    sid_counter = 1
    
    for el in tree.iter():
        is_interactive = False
        
        # Check by tag name
        if el.tag in INTERACTIVE_TAGS:
            is_interactive = True
        # Check by attribute roles
        elif el.get('role') == 'button' or el.get('onclick') is not None:
            is_interactive = True
            
        if is_interactive:
            sid = str(sid_counter)
            el.set('data-sid', sid)
            
            # Extract metadata for action map
            label = get_element_label(el)
            el_type = el.tag
            if el.tag == 'input':
                el_type = f"input[{el.get('type', 'text')}]"
                
            action_info = {
                "id": sid,
                "type": el_type,
                "label": label[:100],  # Truncate long labels
                "href": el.get('href', ''),
                "placeholder": el.get('placeholder', ''),
                "bind_id": el.get('data-sw-bind', ''),
            }
            # Remove empty fields
            action_info = {k: v for k, v in action_info.items() if v}
            action_map.append(action_info)
            sid_counter += 1
            
        # 3. Clean up element attributes (strip styling, classes, etc.)
        attrs = list(el.attrib.keys())
        for attr in attrs:
            if attr not in KEEP_ATTRS:
                del el.attrib[attr]
                
    # Generate clean HTML
    clean_html = etree.tostring(tree, encoding='unicode', method='html')
    # Clean up double newlines and excessive indentation to save tokens
    clean_html = re.sub(r'\n\s*\n', '\n', clean_html)

    groups = group_action_map(tree, action_map)
    action_map = apply_groups_to_actions(action_map, groups)

    return clean_html, action_map, groups

def generate_xml_map(url: str, title: str, action_map: List[Dict[str, Any]]) -> str:
    """Flat Action Map as XML (legacy)."""
    root = etree.Element("page", url=url, title=title)
    for action in action_map:
        action_el = etree.SubElement(root, "action")
        for k, v in action.items():
            action_el.set(k, str(v))

    return etree.tostring(root, encoding='unicode', pretty_print=True)


def generate_grouped_xml_map(
    url: str, title: str, groups: List[Dict[str, Any]]
) -> str:
    """Grouped Action Map as XML for LLM consumption."""
    root = etree.Element("page", url=url, title=title)
    for block in groups:
        group_el = etree.SubElement(root, "group", name=block.get("name", "Page"))
        for action in block.get("elements", []):
            action_el = etree.SubElement(group_el, "action")
            for k, v in action.items():
                action_el.set(k, str(v))

    return etree.tostring(root, encoding='unicode', pretty_print=True)
