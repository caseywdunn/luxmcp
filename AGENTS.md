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
