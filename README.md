# iisg-kb-viewer

A browser-based viewer for the IISG knowledge graph: the merged output of six
ETL pipelines --
[biblio-etl](https://github.com/knaw-iisg/biblio-etl),
[archive-etl](https://github.com/knaw-iisg/archive-etl),
[findingaid-etl](https://github.com/knaw-iisg/findingaid-etl),
[authorities-etl](https://github.com/knaw-iisg/authorities-etl),
[dataverse-etl](https://github.com/knaw-iisg/dataverse-etl) and
[orcid-etl](https://github.com/knaw-iisg/orcid-etl) -- each loaded into its
own named graph by [triplestore](https://github.com/knaw-iisg/triplestore).
Lets you search the graph, browse it by pipeline or by kind of item, and
trace a single record's connections across pipelines.

## Architecture

A single static page (`static/index.html`, vanilla JS, no build step, no
framework) that queries a SPARQL endpoint directly from the browser.
`iisgBrowser.py` is a two-line Flask app that only serves that file --
all the actual work (SPARQL queries, rendering) happens client-side. There's
no backend API of this repo's own, and nothing here holds or caches graph
data.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 iisgBrowser.py
```

Serves on `http://localhost:5000`. Needs a running
[triplestore](https://github.com/knaw-iisg/triplestore) instance to query --
by default it points at `http://localhost:7878` (QLever's default port in
that repo's `Qleverfile`); override with `?endpoint=http://host:port` in the
URL for a differently-hosted store.

## Features

- **Home dashboard**: live per-graph and per-kind-of-item counts, fetched
  from the store on load, clickable to browse.
- **Search**: matches `schema:name`/`rdfs:label`. Mixed-type result sets
  show as compact count-and-icon tiles (like the home dashboard) rather than
  one long list; a tile with exactly one match skips straight to it.
- **Type-specific record views**: Book/media, Archive, Dataset, File
  (`DataDownload`), DataCatalog, and the person/organization/place/etc
  authority family each get a curated set of fields up top, rather than one
  generic property dump for everything. Anything not covered falls back to
  a fully generic view. The full statement list (everything asserted about
  a record, and everything pointing to it) is always one click away via the
  statement-count line in the header, whichever view is showing.
- **Hierarchy breadcrumbs**: findingaid-etl's nested archive components
  (collection > series > ... > file, up to 12 levels via `sdo:isPartOf`) get
  a clickable trail on the record page and a read-only path on matching
  search/browse cards. Deliberately not attempted for Dataverse datasets --
  a dataset can belong to several catalogs at once via
  `sdo:includedInDataCatalog`, a many-to-many membership, not a
  single-parent chain a breadcrumb could represent.
- **Cross-pipeline interlinking, for free**: an entity's page merges
  statements from every graph that asserts something about that same IRI
  (e.g. a person known to both `authorities-etl` and `orcid-etl`), since
  queries aren't graph-scoped unless browsing a specific pipeline.

## Data conventions this viewer works around

- **schema.org scheme**: some pipelines mint `http://schema.org/`, others
  (correctly, per [SCHEMA-AP-NDE](https://docs.nde.nl/schema-profile/))
  `https://schema.org/`. Both are normalized to one `sdo:` prefix throughout,
  so records interlink correctly regardless of which scheme a given
  pipeline happens to use.
- **URL-shaped literals**: some predicates (`nativeViewer` in particular)
  are asserted as `xsd:anyURI`-typed literals instead of real `URIRef`
  nodes in three of the six pipelines. Both forms are treated as clickable
  links.
- **Images**: only ever fetched from `iisg.amsterdam`-hosted URLs the graph
  itself asserts, never hotlinked from a third-party host referenced
  incidentally (e.g. a Dataverse file's own storage) -- and there are none
  yet, so no thumbnails render today. Wired up and ready for the day a
  pipeline asserts one.
