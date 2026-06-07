#!/usr/bin/env python3
"""Functional checks for md_render — run: .venv/bin/python scripts/test_md_render.py

Focus: ```text``` fences render as prose with live links (the email/PR-body case),
while real code fences stay untouched for Prism. Plain bash/python, no framework.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import md_render

fails = 0


def check(name, cond):
    global fails
    print(('PASS' if cond else 'FAIL') + ': ' + name)
    if not cond:
        fails += 1


# A text fence with an explicit [disp](url) link, a bare URL, and literal markdown
# that must NOT be parsed (the ** stays literal in a preformatted block).
src = (
    "intro https://out.example.com/x\n\n"
    "```text\n"
    "From: **Torre** <a@b.com>\n"
    "see [the catalog](https://epic-devcloud.org/prod/pcs/catalog/)\n"
    "and bare https://github.com/BNLNPPS/wrangle-ai\n"
    "```\n\n"
    "```python\n"
    "x = 'https://not-a-link.example/in/code'\n"
    "```\n"
)
html = md_render.render_body(src)

check("text fence loses language-text class", 'class="language-text"' not in html)
check("text fence gets .text-fence wrapper", '<pre class="text-fence"><code>' in html)
check("[disp](url) becomes one anchor",
      '<a target="_blank" href="https://epic-devcloud.org/prod/pcs/catalog/">the catalog</a>' in html)
check("bare URL inside text fence is linkified",
      '<a target="_blank" href="https://github.com/BNLNPPS/wrangle-ai">https://github.com/BNLNPPS/wrangle-ai</a>' in html)
check("literal ** is preserved (not parsed as bold)", '<strong>' not in html)
check("python fence keeps its language class for Prism", 'class="language-python"' in html)
check("URL inside a real code fence is NOT linkified",
      'href="https://not-a-link.example/in/code"' not in html)
check("URL outside any fence still linkifies",
      '<a target="_blank" href="https://out.example.com/x">https://out.example.com/x</a>' in html)

print()
print('ALL PASS' if not fails else f'{fails} FAILED')
sys.exit(1 if fails else 0)
