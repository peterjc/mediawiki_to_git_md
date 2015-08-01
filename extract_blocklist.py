#!/usr/bin/env python
import os
import sys

print("Parse saved HTML file of wiki/Special:BlockList into simple text file")

blocklist_html = sys.argv[1]
output_text = "user_blacklist.txt"

count = 0
with open(blocklist_html) as input_handle:
    with open(output_text, "w") as output_handle:
        for line in input_handle:
            if '<td class="TablePager_col_ipb_target">' not in line:
                continue
            line = line[line.index('<td class="TablePager_col_ipb_target">') + 38:]
            line = line[:line.index('</a>')]
            line = line[line.index('>') + 1:]
            # TODO - unescape &amp; if present?
            output_handle.write("%s\n" % line)
            count += 1
print("Extracted %i users from %r into %r" % (count, blocklist_html, output_text))
