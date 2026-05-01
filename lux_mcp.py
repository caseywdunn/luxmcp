#!/usr/bin/env python3
"""MCP server for Yale's Lux cultural heritage collection API via the luxy library."""

import json
import logging
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP
from luxy import Collections, Concepts, Events, Objects, PeopleGroups, Places, Works
from luxy import api as luxy_api

logging.basicConfig(level=logging.WARNING)

# ---------------------------------------------------------------------------
# Server instructions
# ---------------------------------------------------------------------------
# Sent to the client at initialize and surfaced to the model. Covers tool
# usage and the report-formatting conventions used by Dunn-lab Lux reports.

INSTRUCTIONS = r"""
This server exposes Yale's Lux federated catalogue (people, places, objects,
works, concepts, events, collections) for cultural-heritage and natural-history
research. Always cite the `view_url` returned by each query so the user can
click through to the same query in the Lux UI.

## Report-writing conventions

When the user asks for a *report* or *exhibition plan* grounded in Lux holdings,
follow these conventions so output matches the existing reports in `tmp/`
(`iz_report/`, `botany_report/`, `yale_collections/`, `exhibitions/`,
`drawn_from_life/`):

1. **Draft in Markdown or plain text first.** Get the prose right before
   wrapping it in LaTeX. Pandoc-flavoured Markdown with a YAML header (see
   `tmp/iz_report/invertebrate_report.md`) is the easiest path; a hand-written
   `.tex` is fine for exhibition-style documents (see
   `tmp/exhibitions/exhibitions.tex`).

2. **Render to PDF in the project's LaTeX style.** Match the existing reports:
   - `documentclass[11pt]{article}`, `geometry margin=1in`, `mathpazo` font.
   - `colorlinks=true` with `linkcolor=NavyBlue`, `urlcolor=NavyBlue`.
   - Section numbering off (`\setcounter{secnumdepth}{-\maxdimen}`).
   - `tabularx` for focal-objects tables; `booktabs` rules.
   - Author block: `Prepared by Casey W. Dunn (EEB, dunnlab.org)`.
   - One report per subdirectory of `tmp/` (`.tex` + `.pdf` co-located, plus
     any figure source files).

3. **Place each report in its own `tmp/<report_name>/` directory.** Keep the
   `.tex` and the compiled `.pdf` together. Compile with `tectonic` if
   available, otherwise `pdflatex` / `xelatex`.

4. **Ground every claim in Lux.** Quote accession numbers, call numbers, and
   finding-aid box/folder citations verbatim from the records returned by the
   tools. Note that those records may shift as Lux is re-curated.

5. **Close with a `Preparation` section** that includes the following
   boilerplate, verbatim (LaTeX form):

```
\section{Preparation}\label{preparation}

This document was prepared using \textbf{Claude Opus 4.7} (Anthropic) with
\textbf{luxmcp} (\url{https://github.com/caseywdunn/luxmcp}), an MCP
server that exposes Yale's Lux cultural-heritage and natural-history
catalogue (\url{https://lux.collections.yale.edu/}) to language models.
luxmcp wraps \textbf{luxy} (\url{https://github.com/project-lux/luxy}),
the official Python client for the Lux API.
```

Append a sentence or two describing the specific filters used and the
date the records were retrieved (records may shift as Lux is re-curated).

The Markdown form of the same boilerplate is:

```
## Preparation

This document was prepared using **Claude Opus 4.7** (Anthropic) with
**luxmcp** (<https://github.com/caseywdunn/luxmcp>), an MCP server that
exposes Yale's Lux cultural-heritage and natural-history catalogue
(<https://lux.collections.yale.edu/>) to language models. luxmcp wraps
**luxy** (<https://github.com/project-lux/luxy>), the official Python
client for the Lux API.
```

6. **Update the model name in the boilerplate** if you are not Claude Opus 4.7.
   Substitute the actual model identifier (e.g. ``Claude Sonnet 4.6'').

7. **Cite a `Sources` section** before `Preparation`, listing at minimum the
   Lux URL, any GBIF dataset keys, and links to companion reports in
   sibling `tmp/` directories.

See `AGENTS.md` in the lux-mcp repo for additional operational guidance on
filter syntax, decision trees, and gotchas.
""".strip()

mcp = FastMCP("lux", instructions=INSTRUCTIONS)

# ---------------------------------------------------------------------------
# Entity type registry
# ---------------------------------------------------------------------------

ENTITY_TYPES = {
    "objects": Objects,
    "works": Works,
    "people": PeopleGroups,
    "places": Places,
    "concepts": Concepts,
    "events": Events,
    "collections": Collections,
}


def _get_entity(entity_type: str):
    key = entity_type.lower().rstrip("s")
    # Try exact match first, then singular forms
    cls = ENTITY_TYPES.get(entity_type.lower()) or ENTITY_TYPES.get(key + "s") or ENTITY_TYPES.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown entity type '{entity_type}'. "
            f"Valid types: {', '.join(ENTITY_TYPES)}"
        )
    return cls()


# ---------------------------------------------------------------------------
# Linked Art trimming helpers
# ---------------------------------------------------------------------------

def _label(obj: dict) -> str:
    if isinstance(obj, dict):
        return obj.get("_label") or obj.get("content", "")
    return str(obj)


def _extract_names(identified_by: list) -> list[str]:
    names = []
    for entry in identified_by or []:
        if entry.get("type") in ("Name", "Identifier"):
            content = entry.get("content", "")
            if content:
                names.append(content)
    return names


def _extract_statements(referred_to_by: list, max_chars: int = 400) -> list[str]:
    stmts = []
    for entry in referred_to_by or []:
        content = entry.get("content", "")
        if content:
            stmts.append(content[:max_chars])
    return stmts


def _extract_production(produced_by: dict | None) -> dict:
    if not produced_by:
        return {}
    result = {}
    # Date
    timespan = produced_by.get("timespan", {})
    if timespan:
        result["date"] = timespan.get("_label") or timespan.get("begin_of_the_begin", "")[:10]
    # Maker
    carried_out_by = produced_by.get("carried_out_by", [])
    if carried_out_by:
        result["by"] = [_label(a) for a in carried_out_by]
    # Place
    took_place_at = produced_by.get("took_place_at", [])
    if took_place_at:
        result["place"] = [_label(p) for p in took_place_at]
    return result


def trim_item(data: dict, full: bool = False) -> dict:
    """Return a concise summary of a Linked Art item."""
    if full:
        return data

    out: dict[str, Any] = {
        "id": data.get("id", ""),
        "type": data.get("type", ""),
        "label": data.get("_label", ""),
    }

    names = _extract_names(data.get("identified_by", []))
    if names:
        out["names"] = names[:5]

    stmts = _extract_statements(data.get("referred_to_by", []))
    if stmts:
        out["descriptions"] = stmts[:2]

    # Production / creation info
    produced_by = data.get("produced_by") or data.get("created_by")
    prod = _extract_production(produced_by)
    if prod:
        out["production"] = prod

    # Collections / membership
    member_of = data.get("member_of", [])
    if member_of:
        out["member_of"] = [_label(m) for m in member_of[:5]]

    # Classification / type
    classified_as = data.get("classified_as", [])
    if classified_as:
        out["classified_as"] = [_label(c) for c in classified_as[:5]]

    # Subject (web pages, IIIF manifests)
    subject_of = data.get("subject_of", [])
    web = [s.get("access_point", [{}])[0].get("id", "") for s in subject_of if s.get("access_point")]
    if web:
        out["web_pages"] = [u for u in web if u][:3]

    # Birth/death for people
    for field in ("born", "died"):
        val = data.get(field)
        if val:
            ts = val.get("timespan", {})
            out[field] = ts.get("_label") or ts.get("begin_of_the_begin", "")[:10]

    # Nationality / residence for people
    for field in ("classified_as",):
        pass  # already handled above

    return {k: v for k, v in out.items() if v}


def _build_filters(entity, filters: dict) -> Any:
    """Apply a filters dict to a luxy entity instance.

    Filter values follow these conventions:
    - Simple string/bool/int: passed directly
    - [value, operator] (e.g. ["1987-01-01T00:00:00.000Z", ">="]) → tuple
    - Nested dict: passed as-is for nested object filters
    """
    for key, value in filters.items():
        if isinstance(value, list) and len(value) == 2 and isinstance(value[1], str) and value[1] in (">", ">=", "<", "<=", "==", "!="):
            entity = entity.filter(**{key: (value[0], value[1])})
        else:
            entity = entity.filter(**{key: value})
    return entity


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_filters(entity_type: str) -> str:
    """List available search filters for a Lux entity type.

    Returns filter names, human-readable labels, descriptions, and accepted
    value types so you can construct valid search queries.

    Args:
        entity_type: One of: objects, works, people, places, concepts, events, collections
    """
    entity = _get_entity(entity_type)
    options = entity.get_options()
    lines = [f"Filters for '{entity_type}':\n"]
    for name, info in sorted(options.items()):
        label = info.get("label", name)
        desc = info.get("description", "")
        rel = info.get("relation", "")
        line = f"  {name} ({label}, type={rel})"
        if desc and desc != "No description available":
            line += f"\n    {desc}"
        if "values" in info:
            line += f"\n    Allowed values: {', '.join(repr(v) for v in info['values'])}"
        lines.append(line)
    return "\n".join(lines)


@mcp.tool()
def search(
    entity_type: str,
    filters: dict,
    page: int = 1,
    full_items: bool = False,
) -> str:
    """Search Yale Lux collection for any entity type with flexible filters.

    Returns total count, a view URL for the Lux UI, and up to 20 trimmed
    items from the requested page.

    Args:
        entity_type: One of: objects, works, people, places, concepts, events, collections
        filters: Dict of filter_name → value. For comparison filters use
                 [value, operator] e.g. {"encounteredDate": ["1900-01-01T00:00:00.000Z", ">="]}
                 For nested filters use a dict e.g. {"producedBy": {"name": "Paris"}}
        page: Page number (1-based, 20 items/page)
        full_items: If true, return complete Linked Art JSON instead of trimmed summaries
    """
    entity = _get_entity(entity_type)
    entity = _build_filters(entity, filters)
    result = entity.get()

    total = result.num_results
    pages = result.num_pages()
    page_urls = result.get_page_urls()

    out: dict[str, Any] = {
        "total_results": total,
        "total_pages": pages,
        "view_url": result.view_url,
    }

    if total == 0 or not page_urls:
        out["page"] = 0
        out["items"] = []
        return json.dumps(out, indent=2)

    page = max(1, min(page, len(page_urls)))
    page_data = result.get_page_data(page_urls[page - 1])
    items_raw = result.get_items(page_data)

    trimmed = []
    for item in items_raw:
        try:
            item_data = result.get_item_data(item)
            trimmed.append(trim_item(item_data, full=full_items))
        except Exception as exc:
            trimmed.append({"id": item.get("id", ""), "error": str(exc)})

    out["page"] = page
    out["items"] = trimmed
    return json.dumps(out, indent=2)


@mcp.tool()
def get_item_details(item_uri: str, full: bool = False) -> str:
    """Fetch details for a specific Lux item by its URI.

    Args:
        item_uri: The Lux item URI (e.g. https://lux.collections.yale.edu/data/object/...)
        full: If true, return the complete Linked Art JSON (can be large)
    """
    try:
        session = luxy_api.session
        response = session.get(item_uri)
        response.raise_for_status()
        data = response.json()
        return json.dumps(trim_item(data, full=full), indent=2)
    except requests.HTTPError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}: {item_uri}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def summarize_collection(
    entity_type: str,
    filters: dict,
    max_pages: int = 5,
) -> str:
    """Aggregate statistics for a filtered set of Lux entities.

    Fetches up to `max_pages` pages of results and returns:
    - total count
    - breakdown of classified types
    - date range of production/creation
    - top associated places and makers
    - sample item labels

    Args:
        entity_type: One of: objects, works, people, places, concepts, events, collections
        filters: Search filters (same format as the `search` tool)
        max_pages: How many pages to scan for statistics (default 5, max 20)
    """
    max_pages = min(max_pages, 20)
    entity = _get_entity(entity_type)
    entity = _build_filters(entity, filters)
    result = entity.get()

    total = result.num_results
    pages = result.num_pages()
    page_urls = result.get_page_urls()
    scan_pages = min(max_pages, pages, len(page_urls))

    type_counts: dict[str, int] = {}
    maker_counts: dict[str, int] = {}
    place_counts: dict[str, int] = {}
    collection_counts: dict[str, int] = {}
    years: list[int] = []
    samples: list[str] = []

    for i in range(scan_pages):
        page_data = result.get_page_data(page_urls[i])
        for item in result.get_items(page_data):
            try:
                d = result.get_item_data(item)
            except Exception:
                continue

            # Label sample
            label = d.get("_label", "")
            if label and len(samples) < 10:
                samples.append(label)

            # Type classification
            for cls in d.get("classified_as", []):
                t = _label(cls)
                if t:
                    type_counts[t] = type_counts.get(t, 0) + 1

            # Production info
            prod = d.get("produced_by") or d.get("created_by") or {}
            for maker in prod.get("carried_out_by", []):
                m = _label(maker)
                if m:
                    maker_counts[m] = maker_counts.get(m, 0) + 1
            for place in prod.get("took_place_at", []):
                p = _label(place)
                if p:
                    place_counts[p] = place_counts.get(p, 0) + 1
            ts = prod.get("timespan", {})
            begin = ts.get("begin_of_the_begin", "")
            if begin and len(begin) >= 4:
                try:
                    years.append(int(begin[:4]))
                except ValueError:
                    pass

            # Collections
            for col in d.get("member_of", []):
                c = _label(col)
                if c:
                    collection_counts[c] = collection_counts.get(c, 0) + 1

    def top(d: dict, n: int = 10) -> list[tuple[str, int]]:
        return sorted(d.items(), key=lambda x: -x[1])[:n]

    stats: dict[str, Any] = {
        "total_results": total,
        "total_pages": pages,
        "pages_scanned": scan_pages,
        "view_url": result.view_url,
    }
    if samples:
        stats["sample_labels"] = samples
    if type_counts:
        stats["top_types"] = dict(top(type_counts))
    if maker_counts:
        stats["top_makers"] = dict(top(maker_counts))
    if place_counts:
        stats["top_places"] = dict(top(place_counts))
    if collection_counts:
        stats["top_collections"] = dict(top(collection_counts))
    if years:
        stats["date_range"] = {"earliest": min(years), "latest": max(years)}

    return json.dumps(stats, indent=2)


@mcp.tool()
def search_by_place(
    place_name: str,
    target_entity_type: str = "objects",
    relationship: str = "producedAt",
    extra_filters: dict | None = None,
    page: int = 1,
) -> str:
    """Find Lux entities associated with a geographic place.

    First resolves the place name to a Lux URI, then searches the target
    entity type for items linked to that place via the given relationship.

    Args:
        place_name: Name of the place to search for (e.g. "Netherlands", "Paris", "New Haven")
        target_entity_type: Entity type to search within (objects, works, people, etc.)
        relationship: How the entity relates to the place. Common values:
                      - producedAt (where objects/works were made)
                      - encounteredAt (where objects were found/collected)
                      - startAt / endAt (for people: birth/death place)
                      - tookPlaceAt (for events)
        extra_filters: Additional filters to narrow results
        page: Result page (1-based)
    """
    # Step 1: find the place
    place_result = Places().filter(name=place_name).get()
    if place_result.num_results == 0:
        return json.dumps({"error": f"No place found matching '{place_name}'"})

    page_data = place_result.get_page_data(place_result.get_page_urls()[0])
    place_items = place_result.get_items(page_data)
    if not place_items:
        return json.dumps({"error": f"No place items found for '{place_name}'"})

    # Pick the best match (first result)
    place_item = place_items[0]
    place_uri = place_item.get("id", "")
    place_label = place_item.get("_label", place_name)

    # Step 2: search target entity type filtered by place
    entity = _get_entity(target_entity_type)
    entity = entity.filter(**{relationship: {"id": place_uri}})

    if extra_filters:
        entity = _build_filters(entity, extra_filters)

    result = entity.get()
    total = result.num_results
    pages = result.num_pages()
    page_urls = result.get_page_urls()

    out: dict[str, Any] = {
        "place_matched": place_label,
        "place_uri": place_uri,
        "relationship": relationship,
        "total_results": total,
        "total_pages": pages,
        "view_url": result.view_url,
    }

    if total == 0 or not page_urls:
        out["page"] = 0
        out["items"] = []
        return json.dumps(out, indent=2)

    page = max(1, min(page, len(page_urls)))
    page_data = result.get_page_data(page_urls[page - 1])
    items_raw = result.get_items(page_data)

    trimmed = []
    for item in items_raw:
        try:
            trimmed.append(trim_item(result.get_item_data(item)))
        except Exception as exc:
            trimmed.append({"id": item.get("id", ""), "error": str(exc)})

    out["page"] = page
    out["items"] = trimmed
    return json.dumps(out, indent=2)


@mcp.tool()
def explore_by_person(
    person_name: str,
    entity_type: str = "objects",
    relationship: str = "producedBy",
    page: int = 1,
) -> str:
    """Explore Lux objects, works, or events associated with a person or collector.

    Resolves the person's name to a Lux URI, then finds all entities linked
    to them via the specified relationship.

    Args:
        person_name: Name of the person/organization (e.g. "Picasso", "J.P. Morgan")
        entity_type: What to search for: objects, works, events, collections
        relationship: How the entity relates to the person. Common values:
                      - producedBy (maker of objects/works)
                      - createdBy (author of works)
                      - collectedBy (collector of objects)
                      - memberOf (for collections/groups)
                      - carriedOutBy (for events)
        page: Result page (1-based)
    """
    # Step 1: find the person
    person_result = PeopleGroups().filter(name=person_name).get()
    if person_result.num_results == 0:
        return json.dumps({"error": f"No person/group found matching '{person_name}'"})

    page_data = person_result.get_page_data(person_result.get_page_urls()[0])
    person_items = person_result.get_items(page_data)
    if not person_items:
        return json.dumps({"error": f"No person items returned for '{person_name}'"})

    person_item = person_items[0]
    person_uri = person_item.get("id", "")
    person_label = person_item.get("_label", person_name)

    # Fetch person details for context
    person_detail: dict[str, Any] = {}
    try:
        person_data = person_result.get_item_data(person_item)
        person_detail = trim_item(person_data)
    except Exception:
        pass

    # Step 2: find related entities
    entity = _get_entity(entity_type)
    entity = entity.filter(**{relationship: {"id": person_uri}})
    result = entity.get()

    total = result.num_results
    pages = result.num_pages()
    page_urls = result.get_page_urls()

    out: dict[str, Any] = {
        "person_matched": person_label,
        "person_uri": person_uri,
        "person_detail": person_detail,
        "relationship": relationship,
        "entity_type": entity_type,
        "total_results": total,
        "total_pages": pages,
        "view_url": result.view_url,
    }

    if total == 0 or not page_urls:
        out["page"] = 0
        out["items"] = []
        return json.dumps(out, indent=2)

    page = max(1, min(page, len(page_urls)))
    page_data = result.get_page_data(page_urls[page - 1])
    items_raw = result.get_items(page_data)

    trimmed = []
    for item in items_raw:
        try:
            trimmed.append(trim_item(result.get_item_data(item)))
        except Exception as exc:
            trimmed.append({"id": item.get("id", ""), "error": str(exc)})

    out["page"] = page
    out["items"] = trimmed
    return json.dumps(out, indent=2)


if __name__ == "__main__":
    mcp.run()
