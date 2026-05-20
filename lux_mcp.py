#!/usr/bin/env python3
"""MCP server for Yale's Lux cultural heritage collection API via the luxy library."""

import argparse
import html as _html
import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import requests
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
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
   - **No author byline.** Omit the `\author{...}` line (or set it to
     `\author{}`); the title block carries only the title and date. The
     Casey W. Dunn credit moves to the `Preparation` section, below.
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

This document was prepared using \textbf{<MODEL_NAME>} with
\textbf{luxmcp} (\url{https://github.com/caseywdunn/luxmcp}, written by
Casey W. Dunn, \url{https://dunnlab.org}).
```

Append a sentence or two describing the specific filters used and the
date the records were retrieved (records may shift as Lux is re-curated).

The Markdown form of the same boilerplate is:

```
## Preparation

This document was prepared using **<MODEL_NAME>** with
**luxmcp** (<https://github.com/caseywdunn/luxmcp>, written by
Casey W. Dunn, <https://dunnlab.org>).
```

6. **Substitute your own model identifier for `<MODEL_NAME>` at write-time.**
   Read your current model name, version, and vendor from your own system
   prompt and write a single self-identifying string into the placeholder
   — e.g. ``Claude Opus 4.7 (Anthropic)'', ``Claude Sonnet 4.6 (Anthropic)'',
   ``GPT-5 (OpenAI)'', ``Gemini 2.5 Pro (Google)''. Do not hardcode any
   specific model or vendor from this template; the goal is for the report
   to record the model that actually generated it, regardless of vendor.
   The MCP server cannot detect which model is connected to it, so this
   substitution must come from you.

7. **Cite a `Sources` section** before `Preparation`, listing at minimum the
   Lux URL, any GBIF dataset keys, and links to companion reports in
   sibling `tmp/` directories.

## Retrieving files (DigitalObjects)

`get_item_details` returns a `files` list — each entry has `url`, `label`, and
usually `format` and/or `kind` (`Digital Image`, `Web Page`, `IIIF Manifest`,
`PDF`).

For most needs the dedicated retrieval tools are simpler than rolling your
own `curl`:

- **`fetch_document(uri_or_url, save_to)`** downloads the best available
  digital surrogate to a local path you can `Read`. Accepts a Lux item URI
  (auto-resolves Yale Library IIIF v3 manifests to their `rendering` PDF),
  a IIIF manifest URL, or a direct PDF/image URL. Save into
  `tmp/<report>/sources/<name>.pdf` (PDFs) or `tmp/<report>/figures/`
  (images). One call replaces the curl-and-Read dance.
- **`fetch_finding_aid(uri, include_inventory=False)`** parses Yale's
  ArchivesSpace public finding-aid pages (which 403 most generic crawlers).
  Accepts a Lux archive set URI, an `hdl.handle.net/10079/fa/<id>` handle,
  or an `archives.yale.edu/...` URL. Returns structured JSON with the
  Abstract, Scope and Contents, Biographical/Historical, Dates, Extent,
  Arrangement, Subjects, Persistent URL, and per-section subpage links —
  the level of detail that the parent Lux record almost never includes.
  Use this for any MS-/RU-prefixed call number you encounter in a Lux set.

If you do need to fetch by hand:

- IIIF manifests (`format: application/ld+json` or `application/json`):
  fetch the JSON, walk to `items[*].items[*].items[*].body.id` (or
  `body.service[0].id`) for full-resolution image URLs.
- Web pages: cite the URL; don't scrape unless asked.

Save figures into `tmp/<report_name>/figures/` and PDFs into
`tmp/<report_name>/sources/`, and cite both the file URL and the parent Lux
URI in the `Sources` section. Note that most Yale Library items are
*partially digitized* — the PDF only contains the leaves that have been
imaged.

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


def _digital_objects(data: dict) -> list[tuple[str, dict]]:
    """Yield (source_label, digital_object) pairs from a Linked Art record.

    `subject_of[].digitally_carried_by[]` → web pages, IIIF manifests, PDFs.
    `representation[].digitally_shown_by[]` → image bytes, thumbnails.
    """
    pairs: list[tuple[str, dict]] = []
    for entry in data.get("subject_of", []) or []:
        outer_label = entry.get("_label", "")
        for do in entry.get("digitally_carried_by", []) or []:
            pairs.append((outer_label, do))
    for entry in data.get("representation", []) or []:
        outer_label = entry.get("_label", "")
        for do in entry.get("digitally_shown_by", []) or []:
            pairs.append((outer_label, do))
    return pairs


# Cache resolved IIIF manifest → rendering-PDF lookups for the process lifetime.
# Stores both successes (dict) and failures (None) so we don't re-fetch.
_MANIFEST_PDF_CACHE: dict[str, dict | None] = {}


def _resolve_manifest_pdf(manifest_url: str, timeout: float = 5.0) -> dict | None:
    """Fetch a IIIF v3 manifest and return its top-level rendering PDF, if any.

    Yale Library digitized texts (Beinecke, Sterling, etc.) advertise a
    `rendering` field in their IIIF v3 manifests pointing at a flattened PDF
    of the digitized leaves. Other Yale manifests (Peabody, YUAG) typically
    do not. This helper returns the first `application/pdf` rendering it
    finds, or None on any failure or if no PDF is advertised.
    """
    if manifest_url in _MANIFEST_PDF_CACHE:
        return _MANIFEST_PDF_CACHE[manifest_url]
    result: dict | None = None
    try:
        resp = luxy_api.session.get(manifest_url, timeout=timeout)
        resp.raise_for_status()
        manifest = resp.json()
        for r in manifest.get("rendering", []) or []:
            if r.get("format") == "application/pdf" and r.get("id"):
                label = r.get("label", {})
                # IIIF v3 labels are language-keyed dicts; pick first non-empty
                lbl = ""
                if isinstance(label, dict):
                    for vals in label.values():
                        if vals:
                            lbl = vals[0]
                            break
                result = {
                    "url": r["id"],
                    "format": "application/pdf",
                    "kind": "PDF",
                    "label": lbl or "Download as PDF",
                }
                break
    except Exception:
        result = None
    _MANIFEST_PDF_CACHE[manifest_url] = result
    return result


def _extract_files(data: dict, max_files: int = 8, resolve_pdfs: bool = False) -> list[dict]:
    files: list[dict] = []
    for outer_label, do in _digital_objects(data):
        access = do.get("access_point") or []
        url = access[0].get("id", "") if access else ""
        if not url:
            continue
        kinds = [_label(c) for c in do.get("classified_as", []) or []]
        entry = {
            "url": url,
            "label": do.get("_label") or outer_label,
        }
        if do.get("format"):
            entry["format"] = do["format"]
        if kinds:
            entry["kind"] = kinds[0] if len(kinds) == 1 else kinds
        files.append(entry)
        if len(files) >= max_files:
            break

    # If asked, resolve any IIIF manifest in `files` to the rendering PDF
    # advertised in its top-level `rendering` field. Costs one extra HTTP per
    # distinct manifest URL (cached). Only active when resolve_pdfs=True so
    # bulk callers (search, summarize) don't pay the cost.
    if resolve_pdfs and not any(f.get("format") == "application/pdf" for f in files):
        for entry in list(files):
            fmt = (entry.get("format") or "").lower()
            url = entry.get("url", "")
            if "json" in fmt and ("/manifests/" in url or "/manifest" in url):
                pdf = _resolve_manifest_pdf(url)
                if pdf:
                    files.append(pdf)
                    break  # one rendering PDF is enough
    return files


def trim_item(data: dict, full: bool = False, resolve_pdfs: bool = False) -> dict:
    """Return a concise summary of a Linked Art item.

    If `resolve_pdfs=True`, IIIF manifests in the record's files list will be
    fetched and any top-level rendering PDF surfaced as a first-class file
    entry. Costs one extra HTTP per distinct manifest URL (cached); off by
    default so bulk callers (search, summarize_collection) don't pay.
    """
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

    # Downloadable files: web pages, IIIF manifests (subject_of →
    # digitally_carried_by) and image bytes / thumbnails (representation →
    # digitally_shown_by). Each entry surfaces label, format, kind, and the
    # access_point URL the model can curl.
    files = _extract_files(data, resolve_pdfs=resolve_pdfs)
    if files:
        out["files"] = files

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
        return json.dumps(trim_item(data, full=full, resolve_pdfs=True), indent=2)
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


# ---------------------------------------------------------------------------
# Document & finding-aid retrieval
# ---------------------------------------------------------------------------
# These tools close the loop from "Lux says X exists" to "the agent can read
# X." Lux is a metadata aggregator; the bytes of digitised texts and the full
# contents of finding aids live elsewhere on Yale infrastructure. The two
# tools below front-end that infrastructure with a single, agent-friendly
# surface so reports can reach the source material in one call.

# Yale's ArchivesSpace public site (archives.yale.edu) returns 403 to many
# default HTTP clients but passes with any identifying real-world UA. We
# advertise an honest one that points back at the project.
_RESEARCH_UA = "lux-mcp/0.1 (research; +https://github.com/caseywdunn/luxmcp)"
_FINDING_AID_HOST = "archives.yale.edu"
_HANDLE_HOST = "hdl.handle.net"


def _http_get(
    url: str,
    *,
    timeout: float = 20.0,
    allow_redirects: bool = True,
    accept: str | None = None,
    stream: bool = False,
) -> requests.Response:
    """HTTP GET with the project's research user-agent and sensible defaults."""
    headers = {"User-Agent": _RESEARCH_UA}
    if accept:
        headers["Accept"] = accept
    resp = requests.get(
        url,
        headers=headers,
        timeout=timeout,
        allow_redirects=allow_redirects,
        stream=stream,
    )
    resp.raise_for_status()
    return resp


def _resolve_archives_url(uri: str) -> str:
    """Normalise an input to a canonical archives.yale.edu resource URL.

    Accepts:
    - `https://lux.collections.yale.edu/data/set/...` — fetches the Lux
      record and pulls the first archives.yale.edu access_point out of
      `subject_of[].digitally_carried_by[]`.
    - `https://hdl.handle.net/10079/fa/<id>` — follows the redirect.
    - `https://archives.yale.edu/...` — returned unchanged.
    """
    parsed = urlparse(uri)
    host = parsed.netloc

    if host == _FINDING_AID_HOST:
        return uri
    if host == _HANDLE_HOST:
        # Follow the handle redirect to its archives.yale.edu target.
        resp = _http_get(uri)
        return resp.url
    if host == "lux.collections.yale.edu":
        data = luxy_api.session.get(uri, timeout=15.0).json()
        for _, do in _digital_objects(data):
            for ap in do.get("access_point") or []:
                ap_url = ap.get("id", "")
                if _FINDING_AID_HOST in ap_url:
                    return ap_url
        raise ValueError(
            f"No archives.yale.edu access_point found in Lux record {uri}"
        )
    raise ValueError(
        f"Unsupported URI host '{host}'. Expected lux.collections.yale.edu, "
        f"hdl.handle.net, or archives.yale.edu."
    )


# Section headers exposed by the ArchivesSpace public theme. Order roughly
# matches display order so the JSON reads top-to-bottom like the page.
_FINDING_AID_SECTIONS: list[tuple[str, str]] = [
    ("scope_and_contents", "Scope and Contents"),
    ("dates", "Dates"),
    ("creator", "Creator"),
    ("conditions_governing_access", "Conditions Governing Access"),
    ("conditions_governing_use", "Conditions Governing Use"),
    ("immediate_source_of_acquisition", "Immediate Source of Acquisition"),
    ("arrangement", "Arrangement"),
    ("extent", "Extent"),
    ("language_of_materials", "Language of Materials"),
    ("persistent_url", "Persistent URL"),
    ("abstract", "Abstract"),
    ("biographical_historical", "Biographical / Historical"),
]


def _strip_html(s: str) -> str:
    """Strip tags, decode entities, collapse whitespace."""
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _extract_section(html_text: str, header: str) -> str:
    """Find a `<h_>HEADER</h_>` and return the text up to the next heading."""
    m = re.search(
        rf"<h\d[^>]*>\s*{re.escape(header)}\s*</h\d>",
        html_text,
        flags=re.S | re.I,
    )
    if not m:
        return ""
    start = m.end()
    next_hdr = re.search(r"<h[1-4][^>]", html_text[start:], flags=re.I)
    end = start + next_hdr.start() if next_hdr else min(len(html_text), start + 20000)
    return _strip_html(html_text[start:end])


def _parse_finding_aid_html(html_text: str, source_url: str) -> dict:
    """Extract structured fields from an ArchivesSpace public resource page."""
    out: dict[str, Any] = {"url": source_url, "kind": "finding_aid"}

    h1s = re.findall(r"<h1[^>]*>(.+?)</h1>", html_text, flags=re.S)
    titles = [_strip_html(h) for h in h1s]
    titles = [t for t in titles if t and "Request Details" not in t]
    if titles:
        out["title"] = titles[0]

    subpages: dict[str, str] = {}
    for href in re.findall(
        r'href="(/repositories/\d+/resources/\d+/[a-z_]+)"', html_text
    ):
        slug = href.rsplit("/", 1)[-1]
        if slug not in subpages:
            subpages[slug] = "https://archives.yale.edu" + href
    if subpages:
        out["subpages"] = subpages

    for key, header in _FINDING_AID_SECTIONS:
        text = _extract_section(html_text, header)
        if text:
            out[key] = text[:6000]

    # Subjects: ArchivesSpace renders them as `<ul class="…subjects_list…">`,
    # not below a heading, so match the class directly.
    subj_match = re.search(
        r'<ul[^>]*class="[^"]*subjects_list[^"]*"[^>]*>(.*?)</ul>',
        html_text,
        flags=re.S,
    )
    if subj_match:
        items = re.findall(r"<a[^>]*>(.+?)</a>", subj_match.group(1), flags=re.S)
        items = [_strip_html(i) for i in items if i.strip()]
        items = [i for i in items if i and len(i) < 300]
        if items:
            out["subjects"] = items[:60]

    return out


@mcp.tool()
def fetch_finding_aid(uri: str, include_inventory: bool = False) -> str:
    """Retrieve and parse a Yale Manuscripts & Archives finding aid.

    Lux records for archival sets (MS, RU collections) link to ArchivesSpace
    finding aids at archives.yale.edu but do not catalogue their contents at
    item level. This tool fetches the public finding-aid page and parses
    standard sections (Abstract, Scope and Contents, Biographical/Historical,
    Dates, Extent, Arrangement, Subjects, Persistent URL, etc.) into JSON.

    Args:
        uri: A Lux archive set URI, an `https://hdl.handle.net/10079/fa/...`
             handle, or an `https://archives.yale.edu/repositories/.../...`
             URL.
        include_inventory: If True, also fetch the `/inventory` subpage and
             try to extract any container labels or series titles visible in
             the static HTML. The full container tree is JS-rendered, so this
             is best-effort.
    """
    try:
        archives_url = _resolve_archives_url(uri)
        resp = _http_get(archives_url)
        out = _parse_finding_aid_html(resp.text, archives_url)

        if include_inventory and out.get("subpages", {}).get("inventory"):
            inv_url = out["subpages"]["inventory"]
            try:
                inv_resp = _http_get(inv_url)
                containers = re.findall(
                    r"\b(Box\s+\d+|Folder\s+\d+)\b",
                    _strip_html(inv_resp.text),
                )
                # Deduplicate while preserving order
                seen: set[str] = set()
                containers_unique = [
                    c for c in containers if not (c in seen or seen.add(c))
                ]
                titles = [
                    _strip_html(t)
                    for t in re.findall(
                        r'class="record-title[^"]*"[^>]*>([^<]+)<',
                        inv_resp.text,
                    )
                ]
                if containers_unique or titles:
                    out["inventory"] = {
                        "url": inv_url,
                        "containers_visible": containers_unique[:200],
                        "series_titles": titles[:60],
                        "note": (
                            "ArchivesSpace renders the full tree via JS; "
                            "this is what the static HTML exposes."
                        ),
                    }
            except Exception as exc:
                out["inventory_error"] = str(exc)

        return json.dumps(out, indent=2)
    except requests.HTTPError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}: {uri}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _document_url_from_lux(item_uri: str) -> dict:
    """Return the best digital-document file entry for a Lux item, or {}."""
    data = luxy_api.session.get(item_uri, timeout=15.0).json()
    files = _extract_files(data, max_files=20, resolve_pdfs=True)
    for f in files:
        if "pdf" in (f.get("format") or "").lower():
            return f
    for f in files:
        if (f.get("format") or "").lower().startswith("image/"):
            return f
    return files[0] if files else {}


@mcp.tool()
def fetch_document(uri_or_url: str, save_to: str) -> str:
    """Download a Lux item's digital surrogate to a local file.

    Resolves the input to the best available file:
    - **Lux item URI**: uses the same digital-object extraction as
      `get_item_details`, including Yale Library IIIF v3 manifest →
      `rendering` PDF resolution, and prefers PDF, then image, then the
      first available file.
    - **IIIF manifest URL** (`/manifest`, `.json`): probed for a
      `rendering` PDF; falls back to downloading the manifest JSON itself.
    - **Direct PDF / image URL**: downloaded as-is.

    The bytes are streamed to `save_to` (parent directories are created).
    Returns JSON with `local_path`, `url`, `bytes`, `content_type`, and
    any label/format/kind metadata picked up along the way, so the caller
    can `Read` the file directly.

    Args:
        uri_or_url: Lux URI, IIIF manifest URL, or direct file URL.
        save_to: Path to write the file to (absolute or relative to CWD).
    """
    try:
        parsed = urlparse(uri_or_url)
        host = parsed.netloc
        meta: dict[str, Any] = {"source": uri_or_url}

        if host == "lux.collections.yale.edu":
            f = _document_url_from_lux(uri_or_url)
            if not f:
                return json.dumps(
                    {"error": f"No downloadable file in Lux record {uri_or_url}"}
                )
            url = f["url"]
            for k in ("label", "format", "kind"):
                if k in f:
                    meta[k] = f[k]
        elif "/manifest" in uri_or_url or uri_or_url.endswith(".json"):
            pdf = _resolve_manifest_pdf(uri_or_url)
            if pdf:
                url = pdf["url"]
                for k in ("label", "format", "kind"):
                    if k in pdf:
                        meta[k] = pdf[k]
            else:
                url = uri_or_url
        else:
            url = uri_or_url

        resp = _http_get(url, timeout=120.0, stream=True)
        parent = os.path.dirname(os.path.abspath(save_to))
        if parent:
            os.makedirs(parent, exist_ok=True)
        size = 0
        with open(save_to, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)
                    size += len(chunk)

        meta.update(
            {
                "url": url,
                "local_path": os.path.abspath(save_to),
                "bytes": size,
                "content_type": resp.headers.get("content-type", ""),
            }
        )
        return json.dumps(meta, indent=2)
    except requests.HTTPError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}: {uri_or_url}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _probe_lux_connectivity() -> None:
    """Log reachability of Lux endpoints from this container. Diagnostic only."""
    ua = "Mozilla/5.0 (compatible; luxy/0.0.7; +https://github.com/project-lux/luxy)"
    headers = {"User-Agent": ua, "Accept": "application/json"}
    probes = [
        ("config", "https://lux.collections.yale.edu/api/advanced-search-config"),
        (
            "search",
            'https://lux.collections.yale.edu/api/search/item?q={"text":"siphonophore"}&page=1&pageLength=5',
        ),
        ("data-root", "https://lux.collections.yale.edu/data/"),
    ]
    for name, url in probes:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            logging.warning("LUX_PROBE %s: %s %s", name, r.status_code, r.reason)
        except Exception as exc:  # noqa: BLE001
            logging.warning("LUX_PROBE %s: error %s", name, exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lux MCP server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="serve over streamable HTTP instead of stdio (also enabled by MCP_TRANSPORT=http)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "0.0.0.0"),
        help="bind host for HTTP transport (default: 0.0.0.0, env: HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="bind port for HTTP transport (default: 8000, env: PORT)",
    )
    args = parser.parse_args()

    use_http = args.http or os.environ.get("MCP_TRANSPORT", "").lower() in (
        "http",
        "streamable-http",
    )
    if use_http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        # FastMCP's default DNS-rebinding protection only whitelists
        # localhost, which 421s every request behind Cloud Run / any reverse
        # proxy. Disable it for HTTP mode — the deploy is HTTPS-terminated.
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        )
        _probe_lux_connectivity()
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
