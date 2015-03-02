This is a work in progress quick hack, written in Python.

I am interested in migrating content in MediaWiki to Markdown
for display on GitHub pages using Jekyll:
https://help.github.com/articles/using-jekyll-with-pages/

The idea here is to first prepare a MediaWiki XML dump of the
current wiki contents (including revisions of the current
pages), and turn each revision into a separate git commit
of the page converted into markdown using pandoc.

This uses a crude Python script (calling pandoc and git).
It assume it is running in the base folder of a git
repository on a suitable branch to which it will commit
back-dated changes as markdown files.

Pages which were deleted on the wiki (e.g. spam) are not
wanted (and appear to be excluded from the export XML file).

The user should provide a manual mapping table of MediaWiki
usernames (column one) to names and email address as used
for their GitHub accounts (column two), e.g.::

    AnOther (tab) A. N. Other <a.n.other@example.com>

Spam revisions (and revsions) are also not wanted, and
can be ignored via a blacklist file of usernames (one
per line). This means not every wiki revision will become
a git commit. See helper script ``extract_blocklist.py``
for pulling the names of blocked users from an HTML
download of the wiki's ``Special:BlockList`` page.

Also, some revisions making minor changes to the wiki
formatting may result in no changes to the converted
markdown, and therefore ideally will not result in a git
commit.


TODO
====

* Cope with unicode in the title / filename, e.g. BioPerl
* Squash quick series of git commits from single author to
  a single page (with same or no comment)?
* Skip git commits where there was no change in the markdown
* Post-process pandoc output to fix wiki-links?
* Automatically deal with images uploaded to the wiki?
