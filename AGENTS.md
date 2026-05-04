# AGENTS.md

Operational guide for AI agents using the `lux-mcp` tools to query Yale's Lux collection.

## What Lux is

Lux indexes ~41M records spanning Yale's museums, libraries, and archives. Every record is Linked Art JSON identified by a URI under `https://lux.collections.yale.edu/data/...`. Records cross-reference each other: an *Object* points to a *Place* of production and a *Person* who made it; a *Collection* (set) groups objects.

Seven entity types are exposed: `objects`, `works`, `people` (PeopleGroups — both individuals and organizations), `places`, `concepts`, `events`, `collections`.

## Decision tree: which tool first?

1. **You don't know the filter name** → call `list_filters(entity_type)` first. Never guess. Filters are *not* uniform across entity types (e.g. `producedAt` works on `objects`, but `works` needs `createdAt`).
2. **You want raw records** → `search(...)`.
3. **You want stats / "describe this collection"** → `summarize_collection(...)`. This pages and aggregates server-side so you don't blow context on raw items.
4. **You have a place name** ("things from the Netherlands") → `search_by_place(...)`. It resolves the place to a URI in one step.
5. **You have a person/collector name** → `explore_by_person(...)`. Same pattern.
6. **You have a URI already** → `get_item_details(uri)`.

## Filter syntax

The `filters` argument is a dict.

```python
# simple equality
{"name": "Rembrandt"}

# multiple ANDed filters
{"hasDigitalImage": True, "name": "portrait"}

# nested filter (filter by a related entity)
{"memberOf": {"name": "Yale University Art Gallery"}}
{"producedBy": {"id": "https://lux.collections.yale.edu/data/person/..."}}

# comparison operators (date ranges, dimensions)
{"encounteredDate": ["1900-01-01T00:00:00.000Z", ">="]}
```

`text` is a special full-text filter accepted by most entity types and is often the most forgiving way to start exploring.

## Conventions and gotchas

- **Always cite the `view_url`** in results. The user can click through to see the same query in the Lux UI.
- **Trim before fetching everything.** Default `full_items=False` is almost always correct. Only set `full=True` when you need a Linked Art field that the trimmer drops (custom dimensions, exhibition history, etc.).
- **Pagination.** Each page is up to 20 items. Don't loop through pages from the agent side — use `summarize_collection(max_pages=N)` to do it server-side.
- **Empty results are fine.** Tools return `total_results: 0, items: []` rather than erroring. Don't retry a hopeless query — refine the filter instead.
- **Names are fuzzy; URIs are exact.** When linking entities, prefer the URI (`{"id": "..."}`) over the name once you have it.
- **People are PeopleGroups.** The same entity type covers individuals (Rembrandt) and organizations (J.P. Morgan & Co.). Don't expect a separate "organizations" type.
- **Library vs museum.** A search for "siphonophore" returns library publications *about* siphonophores by default. To get specimens, also filter `memberOf` by the relevant museum collection (e.g. "Invertebrate Zoology Collection, Yale Peabody Museum").

## Retrieving files (DigitalObjects)

`get_item_details` returns a `files` array listing every fetchable resource attached to a record. Each entry has a `url`, a `label`, and usually a `format` (MIME) and/or `kind` (e.g. `Digital Image`, `Web Page`, `IIIF Manifest`, `PDF`).

Two dedicated retrieval tools front-end the messy infrastructure that lives behind those URLs:

- **`fetch_document(uri_or_url, save_to)`** downloads the best digital surrogate to a local path the harness can `Read`. Accepts a Lux item URI (auto-resolves Yale Library IIIF v3 manifests to their `rendering` PDF, prefers PDF over image), a IIIF manifest URL, or a direct PDF/image URL. Streams to `save_to`, returns `{local_path, url, bytes, content_type, format, kind, label}`. **This is the entry point for primary-document research** — one tool call replaces the curl-and-`Read` dance. Save PDFs into `tmp/<report>/sources/<name>.pdf` and images into `tmp/<report>/figures/`. Note that most Beinecke/Sterling items are *partially digitized*; the PDF contains only the imaged leaves.

- **`fetch_finding_aid(uri, include_inventory=False)`** parses Yale's ArchivesSpace public finding-aid pages, which 403 most generic crawlers (including the harness's own WebFetch). Accepts a Lux archive set URI, an `hdl.handle.net/10079/fa/<id>` handle, or an `archives.yale.edu/.../resources/<id>` URL. Returns structured JSON: title, scope and contents, biographical/historical, dates, extent, arrangement, language, persistent URL, subjects, conditions of access/use, immediate source of acquisition, and subpage links. Use this for any MS- or RU-prefixed call number you encounter — the parent Lux record almost never carries the abstract or scope notes.

If you do need to fetch by hand:

- **IIIF manifests** (`format: application/ld+json` or `application/json`): the URL returns JSON describing image services. Fetch the manifest, walk to `items[*].items[*].items[*].body.id` for the canonical image URL, or `body.service[0].id` to build sized variants like `${service_id}/full/!1200,1200/0/default.jpg`.
- **Web pages** (`kind: Web Page`): cite the URL in the report; don't scrape unless the user asks.

Save figures into `tmp/<report_name>/figures/` and PDFs into `tmp/<report_name>/sources/`, alongside the `.tex`, and cite both the file's source URL and the parent Lux URI in the report's `Sources` section. Records may shift as Lux is re-curated, so refetch before final compile if a report sits for a while.

The PDF auto-resolution inside `get_item_details` is gated to single-item lookups because it costs one extra HTTP round-trip per distinct manifest URL. `search`, `summarize_collection`, and `explore_by_person` do not auto-resolve PDFs — they're for finding records, not reading them. To get the PDF for a record, either fetch its details first or call `fetch_document` directly with the Lux URI.

## When the user asks open-ended questions

| Question shape | Suggested approach |
| --- | --- |
| "What's in collection X?" | `summarize_collection` on `objects` filtered by `memberOf: {name: X}`. Report totals, top types, top places, date range, sample labels. |
| "What did person Y produce/collect?" | `explore_by_person(Y, ...)`. Mention birth/death and classifications from `person_detail` for context. |
| "What's from region Z?" | `search_by_place(Z, ...)`. Try the relationships `producedAt`, `encounteredAt` for objects; `createdAt` for works. |
| "Find a specific item" | `search` with `name` or `text`, then `get_item_details(uri, full=True)` on the best match. |
| "Compare two collections" | Two `summarize_collection` calls, then narrate the contrast. |

## Style

Keep responses grounded in returned data. Quote labels and counts; link the `view_url`. Don't speculate about what's in the collection beyond what the tools returned — Lux only shows what's been digitized and ingested, which is a subset of any physical collection.

## Writing reports

When the user asks for a *report* or an *exhibition plan* grounded in Lux holdings, follow the conventions used by the existing reports in `tmp/` (`iz_report/`, `botany_report/`, `yale_collections/`, `exhibitions/`, `drawn_from_life/`). The same guidance is sent to the client at MCP `initialize` (see `INSTRUCTIONS` in `lux_mcp.py`).

1. **Draft in Markdown or plain text first.** Pandoc-flavoured Markdown with a YAML header (see `tmp/iz_report/invertebrate_report.md`) is the easiest path; hand-written `.tex` is fine for exhibition-style documents (see `tmp/exhibitions/exhibitions.tex`).
2. **Render to PDF in the project's LaTeX style.** `documentclass[11pt]{article}`, `geometry margin=1in`, `mathpazo` font, `colorlinks=true` with `linkcolor=NavyBlue` / `urlcolor=NavyBlue`, section numbering off, `tabularx` + `booktabs` for focal-objects tables. **No author byline** — omit `\author{...}` (or set it to `\author{}`) so the title block carries only the title and date. The Casey W. Dunn credit moves to the `Preparation` section.
3. **One report per subdirectory of `tmp/`.** `.tex` and compiled `.pdf` co-located; compile with `tectonic` if available.
4. **Ground every claim in Lux.** Quote accession numbers, call numbers, and finding-aid box/folder citations verbatim. Note that those records may shift as Lux is re-curated.
5. **Close with a `Preparation` section** that includes the following boilerplate verbatim (LaTeX form):

```
\section{Preparation}\label{preparation}

This document was prepared using \textbf{<MODEL_NAME>} with
\textbf{luxmcp} (\url{https://github.com/caseywdunn/luxmcp}, written by
Casey W. Dunn, \url{https://dunnlab.org}).
```

**Substitute your own model identifier for `<MODEL_NAME>`** — read your current model name, version, and vendor from your own system prompt and write a single self-identifying string into the placeholder (e.g. ``Claude Opus 4.7 (Anthropic)'', ``GPT-5 (OpenAI)'', ``Gemini 2.5 Pro (Google)''). Do not hardcode any specific model or vendor from this template; the goal is to record the model that actually generated the report, regardless of vendor. The MCP server cannot see which model is connected, so the substitution must come from you. Append a sentence or two describing the specific Lux filters used and the date the records were retrieved.

6. **Cite a `Sources` section** before `Preparation`, listing at minimum the Lux URL, any GBIF dataset keys, and links to companion reports in sibling `tmp/` directories.
