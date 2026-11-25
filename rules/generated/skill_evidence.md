Generated from `rules/evidence.yaml` and `rules/evidence_schema.json`; do not edit here. Only with the flag `evidence_store` on.

**Rules** (`g2-s3-1`):
- Extract only what the text says. Never add what you know from elsewhere.
- Every entity, claim and relation quotes the text verbatim (at most 300 characters) and gives its line range [first, last] from the line numbers shown. Quote only from the numbered lines; the context before them is for reading only.
- Use a known id from the catalog when the text clearly means that entity; otherwise give a new ref (n1, n2, ...).
- Never infer a relation from two separate sentences without a word that links them.
- Mark modality by the table. Say confidence from 0 to 1.
- Types, relations and predicates only from the lists. Return JSON only, no prose and no code fence.
- Entity types: `person`, `client`, `task`, `decision`, `risk`, `topic`, `document`, `system`, `call`, `date_event`.
- Relations: `blocks`, `depends_on`, `owns`, `about`, `part_of`, `mentions`, `decided_in`, `risk_for`, `waiting_on`, `attended`, `same_as` (`supersedes` only by the PM).
- Claim predicates: `status`, `due`, `owner`, `decided`, `risk_level`, `waiting_for`, `value`, `said`.
- Modality `proposal`: "we will try", "maybe", an offer not accepted (register: modality tentative, status proposal)
- Modality `expectation`: someone waits for or expects something, a due date asked for, not agreed by the owner of the work
- Modality `confirmation`: agreed, decided, done, confirmed by the person who owns it (register: modality agreed; status done)
- Modality `statement`: a plain fact: a number, a date, a description (register: no modality)
- A hedge word anywhere in a quote turns `confirmation` into `proposal`; notes, briefs and other derived sources never confirm (`statement`).
- At most 40 entities, 60 claims and 60 relations per part; quotes at most 300 characters; confidence under 0.7 is a suggestion.
- Output: JSON with `entities`, `claims` and `relations` (`rules/evidence_schema.json`).
- Topic summaries (`g2-s4-1`) are written by the app, never by the skill: at most 12 sentences, each citing the facts it states (f1, f2, ...); a sentence without a valid cite is dropped (`rules/evidence_summary_schema.json`). A summary is derived: never evidence, never a confirmation.
