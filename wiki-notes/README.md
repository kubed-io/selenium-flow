# wiki-notes/

Hand-written prose for the generated action pages.

Every page under [Actions](Actions) is rendered from `openapi.yaml` by
`scripts/generate_wiki.py`, so editing one directly is pointless — the next run
overwrites it. Anything a schema cannot express goes here instead:

    wiki-notes/screenshot.md   ->  appended to the screenshot wiki page

Create `wiki-notes/<tool>.md` and its contents are included near the bottom of that
tool's page, below the generated tables and above the footer links. Regenerating
leaves it alone.

Use it for the things worth saying that a parameter table cannot: why one
approach is cheaper than another, what a failure usually means, the trap that
cost somebody an afternoon. Not for restating a parameter's type.

These live here rather than in the wiki, and that is not tidiness. A GitHub
wiki addresses a page by basename regardless of directory, so `wiki/notes/
screenshot.md` and `wiki/screenshot.md` both answered to `/wiki/screenshot` —
GitHub served the fragment, and the page looked like it had lost everything but
its prose while the file on disk was perfect. `tests/test_wiki.py` now fails on
any such shadowing.
