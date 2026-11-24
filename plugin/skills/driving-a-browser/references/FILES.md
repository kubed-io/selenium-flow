# Files

A file the browser or the server makes is in one of four places, and only Files
lasts. Each listing entry carries its `uri`, a `url` that opens in a browser, and,
for a screenshot, a recording or a download, `keep_with`: the exact
`keep_file` call.

| Place | Holds | Address | Lasts |
|---|---|---|---|
| Files | prints, and every file you keep | `workspace://files/<name>` | until a person deletes it |
| Screenshots | screenshots not kept yet | `workspace://files/screenshots/<name>` | until kept or cleared |
| Recordings | recordings not kept yet | `workspace://files/recordings/<name>` | until kept or cleared |
| Downloads | what the browser downloaded | `session://downloads/<name>`, `session://<name>/downloads/<name>` in a named session | until the browser ends |

A server started with no `DATA_DIR` has Downloads only: keeping, prints and
saved screenshots are refused with a sentence that says so.

## Keeping a file

```
keep_file(uri="session://downloads/report.csv")
→ {"kept": true, "uri": "workspace://files/report.csv", "name": "report.csv", "size": 2048, ...}
```

- A screenshot or a recording moves out of its folder into Files, and lands as
  `name (1)` beside a file of the same name.
- A download is copied, and replaces a file of the same name in Files. The
  browser keeps its own copy until it ends.
- The result's `uri` is the file's address in Files, and the result carries no
  link.
- Keeping is one way: no tool deletes or clears a file. A person does that in
  the admin page.

## Giving a person a link

A person cannot see your tool results, so give them the `url`, or
`![](url)`; it opens without a token. A server with no public address returns a
relative link: put the address you reached the server on in front of it.

- For a kept file, take the `url` from its entry in `workspace://files`, the
  listing `keep_file`'s `uri` is in. Reading a kept file's `uri` returns the
  file itself, not a link, unless it is a video.
- A download's own `url`, from `session://downloads`, opens only while its
  browser lives. Keep the file and give the person the link from Files, above
  all when you are about to call `end_browser`.
- A link opens for an hour by default. Read the listing again for a fresh one.

## Ending a session

`end_browser()` quits the browser and keeps the workspace: its settings and its
page, so `open_session()` later returns there. The browser's downloads
(`session://downloads`, or `session://<name>/downloads`) go with it, so
`keep_file` what you need first.
Screenshots and prints are in the workspace's files from the moment they are
taken.

`open_session(record=true)` films the browser until it ends. The video appears
under `workspace://files/recordings` shortly after; `keep_file` it to keep it.
Ask for a recording only when a person will watch it.

## Prints

```
print(format="pdf", background=true, filename="invoice")
→ file: {name: "invoice.pdf", url: "https://…/kept/…?exp=…&sig=…"}
```

`print` keeps the file in Files and returns its link, with no image for you.

## Uploading a file

```
upload_file(selector={"xpath": "//input[@type='file']"}, text='{"rows": 3}', filename="data.json")
```

Send content you wrote as `text`, unencoded. A file on the server's own disk
cannot be uploaded; a file the workspace has goes by its `workspace://files`,
`session://downloads` or `session://<name>/downloads` URI as `file`. The result has the `filename` and `bytes`
attached.
