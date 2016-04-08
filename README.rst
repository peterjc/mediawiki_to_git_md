This is a work in progress quick hack, written in Python.

I am interested in migrating content in MediaWiki to Markdown
for display on GitHub pages using Jekyll:
https://help.github.com/articles/using-jekyll-with-pages/

The idea here is to first prepare a MediaWiki XML dump of the
current wiki contents (including revisions of the current
pages), and turn each revision into a separate git commit
of the page converted into markdown using pandoc.

This uses a crude Python script (calling pandoc and git).
It assumes it is running in the base folder of a git
repository on a suitable branch to which it will commit
back-dated changes as markdown files.

Pages which were deleted on the wiki (e.g. spam) are not
wanted (and appear to be excluded from the export XML file).

The user should provide a manual mapping table of MediaWiki
usernames (column one) to names and email address as used
for their GitHub accounts (column two), e.g.::

    AnOther (tab) A. N. Other <a.n.other@example.com>

Spam revisions (and non-rollback reverts) are also not
wanted, and can be ignored via a blacklist file of usernames
(one per line). This means not every wiki revision will become
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


MediaWiki Export to XML
=======================

We use the ``dumpBackup.php`` script, the manual for this is
online at https://www.mediawiki.org/wiki/Manual:dumpBackup.php

First, log into your mediawiki instance and find the PHP file
``.../maintenance/dumpBackup.php`` and your ``../LocalSettings.php``
file. Then try::

   $ cd ~
   $ php .../maintenance/dumpBackup.php --conf .../LocalSettings.php --full --include-files --uploads > mediawiki_dump.xml

Note the inclusion of ``--include-files --uploads`` to ensure
the log includes all the images etc.

Assuming you are running the conversion into MarkDown locally,
zip-up and scp the XML dump back to your machine.

MediaWiki Block List
====================

You can save the HTML page of your wiki's ``Special:BlockList`` page
and parse it with::

    $ curl -o blocklist.html "http://example.org/w/index.php/Special:BlockList?wpTarget=&limit=500"

Then run the script from this repository to pull out the user names::

    $ ../mediawiki_to_git_md/extract_blocklist.py block_list.html
    Parse saved HTML file of wiki/Special:BlockList into simple text file
    Extracted 50 users from 'blocklist.html' into 'user_blocklist.txt'

Usernames mapping
=================

You will need to fill this in, try the conversion once to see which
names to focus on collecting::

    $ emacs usernames.txt

This is a simple two column tab separated table, mapping MediaWiki
usernames (column one) to names and email address as used for their
GitHub accounts (column two), e.g.::

    AnOther (tab) A. N. Other <a.n.other@example.com>

MediWiki Conversion
===================

Now run the conversion in your GitHub Pages repository, where git is
already on the right branch and ready for new commits to be made:

    $ ../mediawiki_to_git_md/convert.py mediawiki_dump.xml 
    ============================================================
    Parsing XML and saving revisions by page.
    ============================================================
    Sorting changes by revision date...
    ...

If it works, it will print a summary of the missing usernames which
you should probably add to ``usernames.txt`` and then after resetting
your branches, retry the conversion. e.g.::

    $ git checkout pre_auto_import && git branch -D master && git checkout -b master
    Switched to branch 'pre_auto_import'
    Deleted branch master (was a348cc5).
    Switched to a new branch 'master'
