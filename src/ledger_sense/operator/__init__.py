"""The close desk (spec: BOARD.md TAPE-1) -- a CFO-office terminal shell over
the existing agents. A human types short orders (interactively at ``desk>``,
or as a single one-shot command/free-text order); this package only ever
talks to Agents 1/2/3/4/5 through their already-published CLIs (subprocess)
or the files they read/write -- never by importing matching/routing
internals (law L1's spirit, made explicit for this card).

The desk never writes ``rules.json`` itself -- every promotion still goes
through the existing ``ledger_sense promote --confirm yes-always`` (shelled
out to exactly like a human would type it), so law L14 (explicit human
"yes, always") is enforced by the same code it always was, not a second
copy of it here.
"""
