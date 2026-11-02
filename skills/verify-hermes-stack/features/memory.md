# Hindsight stored facts

A fact is retained and can later be found as a retrieved fact rather than text echoed from the query.

## Sub-features

- `memory-submit` receives an async operation identity for an owned document.
- `memory-complete` observes completion rather than submission alone.
- `memory-document` verifies bank, document ID, tags and positive extracted units.
- `memory-recall` checks the random identifier and value in a document-linked result.
- `memory-plugin` requires a separate real Hermes plugin retain and subsequent recall proof.

## How to get to it (user POV)

Tell Hermes something to remember and ask about it in a later chat. API clients can separately submit a document and query the retained facts.

## Driving it with verify_stack.py

Preconditions: Hindsight and OmniRoute are usable. The manifest owns a unique `verify-hermes-<UUID>` bank. No write or read alias, mirror, extra bank, template or trusted project override may target `jun`.

Run `python $verifier memory --run $run` for the API path. Require the completed operation, correct document and matching recalled result.

Run `python $verifier plugin-memory --run $run` for the integrated path. The first Hermes session must actually call `hindsight_retain`. The verifier finds a tagged positive-unit document and a recall fact linked to it. A new Hermes session must call `hindsight_recall` and return the random value. Neither the second prompt nor its tool query may contain that value. The provider's actual resolved banks are checked before writing, and installation metadata hashes must remain unchanged.

## Gotchas

The settings audit reads PostgreSQL's effective fsync, full_page_writes, synchronous_commit and data directory. It checks the next-start pg0 durability policy separately and reports a pending API environment refresh without treating the currently healthy database as failed.

Retain is asynchronous and can wait behind production work. A timed-out pending API operation is INCONCLUSIVE. Searching a serialized response can match the request query and falsely pass. The installed explicit retain tool does not return an operation ID, so the plugin path requires persisted extracted facts and linked document evidence rather than inventing an operation identity. Test banks are preserved; cleanup never deletes production memory or derived observations.
