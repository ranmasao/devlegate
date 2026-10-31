# Human table layout audit

The shared output functions are used only by the CLI. Grids cover active and
failed executions, repositories, eligible, blocked, review and accepted tickets,
and preflight checks. Key/value tables cover lifecycle, reconciliation, ready
state, repository diagnostics, dirty details and summary, ticket storage, next
runnable, and control diagnostics. Status and check tests in test_output.py and
test_status_projection.py exercise these paths. Plan and other CLI output are
plain text, not tables. JSON and YAML go through render_machine and their data
and IPC schemas are independent of table layout.

Previously all columns used Python string length and active execution titles
were wrapped at 23 characters into separate rows. For example, a long title
produced this logical structure (borders omitted):

    Ticket | Title
    -------+------------------------
    TASK-1 | a long execution title
    -------+------------------------
           | with a continuation

Now continuation lines are part of the same row:

    Ticket | Title
    -------+------------------------
    TASK-1 | a long execution title
           | with a continuation

On a TTY the renderer subtracts three cells per column plus one for the frame.
Title, reason, detail and description are word-wrapped; other grid columns are
fixed. Key/value labels are fixed and values wrap at spaces. Tokens are never
split or truncated, so IDs, hashes, URLs, refs and paths stay intact. Wrappable
columns have a 12-cell preferred minimum, raised to the longest token. If these
minimum widths plus the frame exceed the terminal, the complete table overflows
instead of misrepresenting values. Non-TTY output keeps natural widths and does
not automatically wrap; explicit newlines still create continuation lines.

Allocation scores the sum of logical row heights (maximum cell height in each
row), then the weighted shrink penalty, then favors earlier columns. A greedy
one-cell reduction and pairwise exchanges jointly consider all rows; this is a
bounded heuristic, not an optimal solver. At most 256 reductions after a coarse
proportional reduction and 256 exchange rounds are performed. Each exchange
round evaluates at most N squared candidates, each scanning all cells, so very
large grids can still cost more than ordinary status tables. Candidate cell
heights are cached. A local 8-column, 20-row test at 110 columns took 0.33s;
this is a representative stress case, not a fixed runtime guarantee.

Display width counts characters with a nonzero Unicode combining class as zero,
East Asian wide/fullwidth characters as two, and all others as one. This is a
bounded approximation: combining marks with class zero, variation selectors,
emoji sequences, and terminal-specific ambiguous widths can differ from the
terminal's actual rendering. Format controls, including zero-width joiners, are
escaped before measurement, so joined emoji are deliberately shown separately.
Control characters, including ANSI escapes, are shown as escaped text rather
than interpreted. Tabs and carriage returns are escaped; newlines are structural.
Table headings are not constrained by the grid width. There is no fixed 23-cell
title width: TTY titles use the allocated space and pipes retain natural width.
