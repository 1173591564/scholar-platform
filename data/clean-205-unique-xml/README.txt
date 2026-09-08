clean-205-unique-xml

Contains 205 unique, strict-clean LaTeXML records selected from the 220 clean records.
Deduplication uses XML canonicalization (C14N) followed by SHA-256, so attribute-order-only differences are treated as identical.
For each duplicate group, the lexicographically earliest ULID is retained.
Directory layout: <paper_id>/latexml/paper.xml
manifest.csv lists retained records; duplicates.csv maps 15 removed record IDs to retained IDs.
