# lux-mcp

An MCP server exposing Yale's [Lux](https://lux.collections.yale.edu) cultural-heritage and natural-history catalog to Claude (or any MCP client). It wraps [`luxy`](https://github.com/project-lux/luxy), the official Python wrapper for the Lux API, and gives the model a small composable tool surface for searching, summarizing, and drilling into Lux entities.

Lux indexes roughly 41 million records across the Yale Peabody Museum, the Yale University Art Gallery, the Yale Center for British Art, and Yale University Library, plus their associated people, places, concepts, and events.

## What's here

| File | Purpose |
| --- | --- |
| `lux_mcp.py` | The MCP server. Run it directly to expose the tools over stdio. |
| `test_lux_mcp.py` | Live integration tests that hit the real Lux API. |
| `AGENTS.md` | Operational guide for AI agents calling this server. |

## Tools

| Tool | Use it for |
| --- | --- |
| `list_filters(entity_type)` | Discover every filter the API accepts for a given entity type, with descriptions and accepted value types. Always call this before guessing filter names. |
| `search(entity_type, filters, page, full_items)` | Generic filtered search. Returns total count, a Lux UI view URL, and up to 20 trimmed records per page. |
| `get_item_details(item_uri, full)` | Fetch one record by URI. `full=True` returns the raw Linked Art JSON. Auto-resolves Yale Library IIIF v3 manifests to their `rendering` PDF and surfaces it in the `files` list. |
| `summarize_collection(entity_type, filters, max_pages)` | Aggregate stats over up to N pages: totals, top types, top makers, top places, sample labels, date range. |
| `search_by_place(place_name, target_entity_type, relationship, ...)` | Resolve a place name to a Lux URI, then find entities linked to it (objects produced/encountered there, people born/died there, etc.). |
| `explore_by_person(person_name, entity_type, relationship, page)` | Resolve a person and find related objects, works, events, or collections (created by, collected by, member of). |
| `fetch_finding_aid(uri, include_inventory)` | Fetch and parse a Yale Manuscripts & Archives finding aid (ArchivesSpace public). Accepts a Lux archive set URI, an `hdl.handle.net/10079/fa/...` handle, or an `archives.yale.edu/...` URL. Returns structured JSON: title, dates, abstract, biographical/historical, scope, arrangement, extent, subjects, persistent URL, subpage links. |
| `fetch_document(uri_or_url, save_to)` | Download the best digital surrogate (PDF or image) of a Lux item or any Yale Library URL to a local file the harness can `Read`. Resolves Yale Library IIIF manifests to their `rendering` PDF; otherwise downloads the URL as-is. |

Entity types: `objects`, `works`, `people`, `places`, `concepts`, `events`, `collections`.

## Setup

```bash
pip install luxy mcp requests
```

The server speaks MCP over stdio; you don't run it directly — your MCP client (Claude Code or Claude Desktop) launches it.

## Using locally

### Claude Code

A `.mcp.json` is checked in at the repo root, so Claude Code auto-discovers the server when launched from this directory:

```bash
cd /path/to/lux-mcp
claude            # the lux server is registered automatically
```

Inside Claude Code, run `/mcp` to confirm the `lux` server is connected and to inspect its tools.

If you'd rather register it globally (available from any directory):

```bash
claude mcp add lux python /absolute/path/to/lux_mcp.py
```

### Claude Desktop

Edit `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`) and add:

```json
{
  "mcpServers": {
    "lux": {
      "command": "python",
      "args": ["/absolute/path/to/lux_mcp.py"]
    }
  }
}
```

Use an absolute path here — Claude Desktop doesn't run from your shell's working directory. If `python` isn't on Claude Desktop's PATH, point at the full interpreter path (e.g. `/opt/conda/bin/python` or the output of `which python`).

Restart Claude Desktop and look for the hammer icon to confirm the tools loaded.

### Do I need `.mcp.json`?

It's optional but useful:

- **Yes**, if you're using Claude Code and want zero-config startup in this directory, or want to share the config with collaborators (it's repo-tracked).
- **No**, if you're using Claude Desktop (it ignores `.mcp.json` and reads its own config) or you've registered the server globally with `claude mcp add`.

### Sanity-checking the server without a client

`python lux_mcp.py` will start the server and block waiting for stdio messages — that's expected. To verify the underlying tools work without setting up a client, run the test suite (`python test_lux_mcp.py`); it imports the tool functions directly and exercises them against the live Lux API.

## Tests

```bash
python test_lux_mcp.py
```

Tests exercise each tool against the real Lux API. They print summaries so you can eyeball correctness and exit non-zero on failure. Currently includes a real-world check that the Yale Peabody invertebrate-zoology siphonophore holdings come back as expected.

## Worked example: Peabody siphonophore collection

```python
summarize_collection(
    "objects",
    {"memberOf": {"name": "Invertebrate Zoology Collection, Yale Peabody Museum"},
     "text": "siphonophora"},
    max_pages=2,
)
```

returns 17 specimens — mostly the genus *Physophora* (including *P. hydrostatica*) and the suborder Physonectae — collected on cruises like R/V Edwin Link and R/V Oceanus, alongside ROV Johnson-Sea-Link dives in the Atlantic off Florida and Pacific off Alaska. The result is what's currently digitized in Lux; the underlying Peabody IZ catalog is much larger.

## Design notes

- Lux returns verbose [Linked Art](https://linked.art) JSON. Tools trim by default (`label`, `id`, `type`, names, descriptions, production info, collection membership) and offer a `full=True` escape hatch.
- `list_filters` is the discovery primitive — filter names vary by entity type (e.g. `producedAt` exists on `objects`, but `works` uses `createdAt`). Call it before composing a query.
- Two-step lookups (place → objects, person → works) are wrapped as single tools so the model doesn't have to chain calls manually.
- Results from queries with zero hits return cleanly (`items: []`, `page: 0`) rather than crashing.
