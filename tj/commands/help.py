"""Help command for tj."""

import sys


def handle_help(args) -> None:
    """Display concise command reference."""
    help_text = """tj - A memory-augmenting knowledge DB and me descriptor

STATUS                          LIST/FILTER
tj              Status          tj l                     All entries
tj hey          Need to know    tj a                     All (no truncation)
tj config       Show config     tj l [ai|b|do|j|m|p|log] By type
tj <N>          List N entries  tj l [Nd|N|-N]           By time/count
                                tj l =ctx :tag p=N s=val Composite
                                tj l [c|t|@]             Metadata (c/t/@=named)

CREATE                          MODIFY
tj <text>       Memory          tj . <text>     Sub-item of last
tj m <text>     Memory          tj + <item>     Add to list
tj do <text>    Todo            tj e            New in $EDITOR
tj p <text>     Profile fact    tj e [ai|do|p|b|j] Type in $EDITOR
tj ai <text>    AI guideline    tj e <n>        Edit entry <n>
tj <url> <text> Bookmark        tj e <n> -k     Edit, keep mod time
                                tj e <n> =ctx   Set context only
                                tj s <n|@name>  Show entry
CONTEXT                         tj s =ctx       Show context
tj =ctx         Switch/create   tj t <n> <tag>  Tag entry
tj =ctx -t ...  With title      tj t- <n> <tag> Remove tag
tj =ctx -t .. -d .. With desc   tj mv <n> <ctx> Move to context
tj =0           Clear context   tj ^ <n>        Pin to top
                                tj d <n|@name>  Delete

CALENDAR                        NUMBERED SHORTCUTS
tj j tomorrow <text>            tj <n> @name    Assign name
tj j mon 10am   Team meeting    tj <n> =ctx     Set context
tj j 0615 14:30 Doctor appt     tj <n> =0       Clear context
tj c [t|w|m]    View calendar   tj <n> :tag     Add tag
tj c t+1        Tomorrow        tj <n> p=N      Set priority
tj c w-1        Last week       tj <n> p=0      Remove priority
tj c 4          Next 4 weeks    tj <n> s=val    Set status
                                tj <n> k=type   Change kind (ai,b,do,j,m,p)
                                tj <n> l=N      Set truncation lines
                                tj <n> l=0      Remove truncation
                                tj cp <n> <datetime> Copy journal entry

METADATA (inline)               DATA
@name           Named entry     tj dump         Export as commands
p=N             Priority        tj admin        Admin commands
s=status        Status value    tj sync         Force sync
:tag            Tag
//url [T](url)  Links           SETUP
-f file         File input      tj() { ~/github/tjrepo/tjai/tj.py "$*"; }
                                Add to ~/.bashrc

AI GUIDELINES
tj ai           Show universal + context guidelines
tj ai =ctx      Show universal + specific context
tj ai <text>    Create universal guideline
tj ai =ctx <text> Create context-specific guideline

Examples:
  tj =tjai @roadmap v2 planning p=1 s=active :design
  tj =projectX Meeting notes //https://doc.url :important
  tj j fri 15:00 Sprint review
  tj l p=1 s=active :urgent
  tj l 10     Last 10 entries | tj l -10   First 10 entries
  tj l 7d     Last 7 days     | tj l 1d    Today (last 24hrs)
"""

    print(help_text, file=sys.stdout)
