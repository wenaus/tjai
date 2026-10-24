# Poke

Poke holds short requests for attention and retained proposals across LLM
sessions. Capcom exposes it beside Pouch and Inflight. Each item has a stable
URL, including after completion: `/tjai/poke/<entry_id>/` opens that item in
Capcom. References to a poke use its complete clickable item URL.

## Content

One issue has one record. A meaningful title is followed by two to four
sentences explaining the situation, why it matters, and the exact question or
recommended action. Aim for 30–60 words, limited to 100 words and 900 characters;
supporting evidence belongs in links. Each item names a responsible session or
person. Collaborator requests retain their authorship and confer no operator
approval.

Needs you contains current decisions and reviews. Keep contains retained
proposals. Deferred items return on their selected date. Approval stays visible
until an owner picks it up into a linked Inflight activity. Done and dismissed
records leave the live list but retain their URLs and version history.

## Keep maintenance

Keep is ordered oldest first and shows time retained, plus entries and exits
over the last seven days. When adding to Keep, review the oldest item and existing related items:
merge duplicates, promote actionable proposals, and close completed or
superseded proposals. Seek an explicit decision before abandoning a still
useful proposal. Routine edits do not reset an item's age. Nothing expires or
is approved automatically. No reminder agent or outgoing notification stream
is installed.

## Shared access

`get_pokes` reads the list or one item. `save_poke` creates or updates one item;
updates require its current `modified_ts`. `poke_action` promotes, retains,
defers, approves, picks up, completes, dismisses or reopens an item. Actions
also require the current revision, so concurrent sessions cannot overwrite
another decision. An LLM recording approval cites the original human dialog
entry; a peer delivery does not qualify. Pickup requires a real open Inflight
activity. The browser and MCP use the same functions in `tjai_app/poke.py`.

Records are ordinary versioned memory entries with `data.poke` metadata.
Creation is serialized by the readable identifier; updates hold the entry's
row lock and use the existing versioned entry writer. No schema migration is
needed. Authenticated reads do not mark items resolved or rewrite state.

## Session practice

Read relevant open pokes before creating one. Maintain the current explanation
instead of accumulating progress reports. An item needing the operator's
attention is mentioned with a short explanation and its item URL. Read receipt
is not completion. Once work starts, link its Inflight activity and remove the
poke from the attention list through pickup. General work tracking remains in
Inflight; Pouch carries deliverables. Existing authorization remains sufficient:
routine authorized work does not acquire an additional approval requirement.
Capture material unresolved decisions and proposals; routine communication and
bulk lists of speculative ideas do not belong in Poke.
