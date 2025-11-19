"""Help command for tj."""

import sys


def handle_help(args) -> None:
    """Display concise command reference."""

    help_text = """tj - Personal AI Memory Aid

STATUS                          QUERY
tj              Status          tj q [b|r|d|p]           By type
tj hey          Need to know    tj q [t|w|m]             By time
tj config show  Show config     tj q =ctx :tag p=N s=val Composite
                                tj l [c|t|p|b|d|ai]      List items
CREATE
tj <text>       Memory
tj d <text>     Todo            MODIFY
tj p <text>     Profile fact    tj . <text>     Sub-item of last
tj ai <text>    AI guideline    tj + <item>     Add to list
tj <url> <text> Bookmark        tj e            New in $EDITOR
                                tj e [ai|d|p|b|j] Type in $EDITOR
CONTEXT                         tj e <n>        Edit entry <n>
tj =ctx         Switch/create   tj s <n|@name>  Show entry
tj =ctx -t ...  With title      tj t <n> <tag>  Tag entry
tj =ctx -t .. -d .. With desc   tj m <n> <ctx>  Move to context
tj =0           Clear context   tj ^ <n>        Pin to top
                                tj x <n|@name>  Delete
CALENDAR                        tj <n> @name    Assign name
tj j tomorrow <text>            tj <n> p=N      Set priority
tj j mon 14:30 Team meeting     tj <n> s=val    Set status
tj j 0615 10:00 Doctor appt
tj c [t|w|m]    View calendar   DATA
tj c t+1        Tomorrow         tj dump         Export as commands
tj c w-1        Last week       tj backup       Manual backup
                                tj sync         Force sync

METADATA (inline)               SETUP
@name           Named entry     tj() { ~/github/tjrepo/tjai/tj.py "$*"; }
p=N             Priority        Add to ~/.bashrc or ~/.zshrc
s=status        Status value
:tag            Tag
//url [T](url)  Links
-f file         File input

Examples:
  tj =tjai @roadmap v2 planning p=1 s=active :design
  tj =projectX Meeting notes //https://doc.url :important
  tj j fri 15:00 Sprint review
  tj q p=1 s=active :urgent
"""

    print(help_text, file=sys.stdout)
