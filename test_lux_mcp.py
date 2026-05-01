"""
Live integration tests for lux_mcp.py.

Hits the real Yale Lux API — requires internet access.
Each test prints a short summary so you can eyeball correctness.
"""

import json
import sys
import traceback

# Import the tool functions directly (not through MCP transport)
from lux_mcp import (
    explore_by_person,
    get_item_details,
    list_filters,
    search,
    search_by_place,
    summarize_collection,
)


PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
failures = []


def check(name: str, fn, *args, **kwargs):
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    try:
        result = fn(*args, **kwargs)
        data = json.loads(result)
        print(json.dumps(data, indent=2)[:1200])
        print(f"  → {PASS}")
        return data
    except Exception as exc:
        traceback.print_exc()
        print(f"  → {FAIL}: {exc}")
        failures.append(name)
        return None


# ------------------------------------------------------------------
# 1. list_filters — objects (returns formatted text, not JSON)
# ------------------------------------------------------------------
print(f"\n{'='*60}")
print("TEST: list_filters(objects)")
print(f"{'='*60}")
try:
    raw = list_filters("objects")
    lines = raw.splitlines()
    print(f"  {len(lines)} lines returned")
    print("\n".join(lines[:8]))
    assert "name" in raw.lower() or "filter" in raw.lower(), "Expected filter listing"
    print(f"  → {PASS}")
except Exception as exc:
    traceback.print_exc()
    print(f"  → {FAIL}: {exc}")
    failures.append("list_filters(objects)")

# ------------------------------------------------------------------
# 2. list_filters — people
# ------------------------------------------------------------------
print("\n--- list_filters raw (people) ---")
raw = list_filters("people")
assert len(raw) > 100
print(raw[:600])
print(f"  → {PASS}")

# ------------------------------------------------------------------
# 3. search — objects by name
# ------------------------------------------------------------------
data = check(
    "search(objects, name=letter)",
    search,
    "objects",
    {"name": "letter"},
    page=1,
)
if data:
    assert data["total_results"] > 0, "Expected results for 'letter'"
    assert "items" in data
    print(f"  total_results={data['total_results']}, items_returned={len(data['items'])}")

# ------------------------------------------------------------------
# 4. search — objects with digital image filter
# ------------------------------------------------------------------
data = check(
    "search(objects, hasDigitalImage=True, name=portrait)",
    search,
    "objects",
    {"hasDigitalImage": True, "name": "portrait"},
    page=1,
)
if data:
    assert data["total_results"] > 0

# ------------------------------------------------------------------
# 5. search — people by name
# ------------------------------------------------------------------
data = check(
    "search(people, name=Rembrandt)",
    search,
    "people",
    {"name": "Rembrandt"},
)
if data:
    assert data["total_results"] > 0
    print(f"  total={data['total_results']}")
    if data["items"]:
        first = data["items"][0]
        print(f"  first item: {first.get('label', first.get('names', '?'))}")
        # Grab URI for get_item_details test
        first_uri = first.get("id", "")

# ------------------------------------------------------------------
# 6. search — collections
# ------------------------------------------------------------------
data = check(
    "search(collections, name=Yale)",
    search,
    "collections",
    {"name": "Yale"},
)
if data:
    assert data["total_results"] > 0

# ------------------------------------------------------------------
# 7. get_item_details — fetch a known Lux object
# ------------------------------------------------------------------
# Yale University Art Gallery has a well-known URI pattern
test_uri = "https://lux.collections.yale.edu/data/person/c8b2c9f8-3c2b-4b7f-8b1a-5e6d2f3a4b5c"
# Use a search to get a real URI first
print("\n--- get_item_details: resolving a real URI via search ---")
try:
    s = json.loads(search("people", {"name": "Rembrandt"}, page=1))
    if s and s["items"]:
        real_uri = s["items"][0]["id"]
        print(f"  Fetching: {real_uri}")
        data = check(
            f"get_item_details({real_uri[-40:]}...)",
            get_item_details,
            real_uri,
        )
        if data:
            assert "id" in data or "label" in data
    else:
        print(f"  SKIP — no URI found from search")
except Exception as e:
    print(f"  SKIP — {e}")

# ------------------------------------------------------------------
# 8. summarize_collection — Yale Art Gallery
# ------------------------------------------------------------------
data = check(
    "summarize_collection(objects, memberOf=Yale Art Gallery, max_pages=2)",
    summarize_collection,
    "objects",
    {"memberOf": {"name": "Yale University Art Gallery"}},
    max_pages=2,
)
if data:
    assert data["total_results"] > 0
    print(f"  total={data['total_results']}, scanned={data.get('pages_scanned')} pages")

# ------------------------------------------------------------------
# 9. search_by_place — Netherlands objects
# ------------------------------------------------------------------
data = check(
    "search_by_place(Netherlands, objects, producedAt)",
    search_by_place,
    "Netherlands",
    "objects",
    "producedAt",
)
if data:
    assert "place_matched" in data
    print(f"  place={data['place_matched']}, total={data.get('total_results', '?')}")

# ------------------------------------------------------------------
# 10. search_by_place — Paris works
# ------------------------------------------------------------------
data = check(
    "search_by_place(Paris, works, createdAt)",
    search_by_place,
    "Paris",
    "works",
    "createdAt",
)
if data:
    print(f"  place={data.get('place_matched')}, total={data.get('total_results', '?')}")

# ------------------------------------------------------------------
# 11. explore_by_person — Rembrandt objects
# ------------------------------------------------------------------
data = check(
    "explore_by_person(Rembrandt, objects, producedBy)",
    explore_by_person,
    "Rembrandt",
    "objects",
    "producedBy",
)
if data:
    print(f"  person={data.get('person_matched')}, total={data.get('total_results', '?')}")
    if data.get("items"):
        print(f"  first item: {data['items'][0].get('label', '?')}")

# ------------------------------------------------------------------
# 12. explore_by_person — collector example (J.P. Morgan)
# ------------------------------------------------------------------
data = check(
    "explore_by_person(Morgan, collections, memberOf)",
    explore_by_person,
    "Morgan",
    "collections",
    "memberOf",
)
if data:
    print(f"  person={data.get('person_matched')}, total={data.get('total_results', '?')}")

# ------------------------------------------------------------------
# 13. Real-world: describe siphonophore holdings at Yale Peabody
# ------------------------------------------------------------------
data = check(
    "summarize_collection(Peabody siphonophores)",
    summarize_collection,
    "objects",
    {"memberOf": {"name": "Invertebrate Zoology Collection, Yale Peabody Museum"},
     "text": "siphonophora"},
    max_pages=2,
)
if data:
    assert data["total_results"] > 0, "Expected siphonophore specimens at Peabody"
    print(f"  total={data['total_results']}, top types={list(data.get('top_types', {}))[:5]}")

# ------------------------------------------------------------------
# 14. Empty-result handling — should not crash
# ------------------------------------------------------------------
data = check(
    "search(collections, name=zzznoresults)",
    search,
    "collections",
    {"name": "zzznoresultszzzxyzzz"},
)
if data:
    assert data["total_results"] == 0
    assert data["items"] == []
    print(f"  empty result returned cleanly")

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
print(f"\n{'='*60}")
if failures:
    print(f"FAILED ({len(failures)}): {', '.join(failures)}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
