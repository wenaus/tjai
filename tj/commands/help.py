"""Help command for tj."""

import sys


def handle_help(args) -> None:
    """Display concise command reference."""
    from tj.state import display_context
    display_context()

    help_text = """tj - A memory-augmenting knowledge DB and me descriptor

STATUS                          LIST/FILTER
tj              Status          tj l                     All entries
tj hey          Need to know    tj l [ai|b|d|j|m|p]      By type (default=m)
tj config       Show config     tj l [t|w|N]             By time (N=days)
                                tj l =ctx :tag p=N s=val Composite
                                tj l [c|t|@]             Metadata (c/t/@=named)
CREATE
tj <text>       Memory
tj m <text>     Memory (default)
tj do <text>    Todo
tj p <text>     Profile fact    MODIFY
tj ai <text>    AI guideline    tj . <text>     Sub-item of last
tj <url> <text> Bookmark        tj + <item>     Add to list
                                tj e            New in $EDITOR
CONTEXT                         tj e [ai|do|p|b|j] Type in $EDITOR
tj =ctx         Switch/create   tj e <n>        Edit entry <n>
tj =ctx -t ...  With title      tj s <n|@name>  Show entry
tj =ctx -t .. -d .. With desc   tj t <n> <tag>  Tag entry
tj =0           Clear context   tj mv <n> <ctx> Move to context
                                tj ^ <n>        Pin to top
                                tj d <n|@name>  Delete
CALENDAR                        tj <n> @name    Assign name
tj j tomorrow <text>            tj <n> p=N      Set priority
tj j mon 10am   Team meeting    tj <n> s=val    Set status
tj j 0615 14:30 Doctor appt
tj c [t|w|m]    View calendar   DATA
tj c t+1        Tomorrow        tj dump         Export as commands
tj c w-1        Last week       tj backup       Manual backup
tj c 4          Next 4 weeks    tj sync         Force sync

METADATA (inline)               SETUP
@name           Named entry     tj() { ~/github/tjrepo/tjai/tj.py "$*"; }
p=N             Priority        Add to ~/.bashrc
s=status        Status value
:tag            Tag
//url [T](url)  Links
-f file         File input

Examples:
  tj =tjai @roadmap v2 planning p=1 s=active :design
  tj =projectX Meeting notes //https://doc.url :important
  tj j fri 15:00 Sprint review
  tj l p=1 s=active :urgent
"""

    print(help_text, file=sys.stdout)
