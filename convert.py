#!/usr/bin/env python
import os
import sys
import subprocess
import sqlite3
import base64
import gzip
import re
from xml.etree import cElementTree as ElementTree

prefix = "wiki/"
mediawiki_ext = "mediawiki"
markdown_ext = "md"
user_table = "usernames.txt"
user_blacklist = "user_blocklist.txt"
default_email = "anonymous.contributor@example.org"
base_url = "http://www.open-bio.org/" # Used for images etc; prefix is appended to this!
base_image_url = base_url + "w/images/" # Used for images
page_prefixes_to_ignore = ["Help:", "MediaWiki:", "Talk:", "User:", "User talk:"] # Beware spaces vs _
default_layout = "wiki" # Can also use None; note get tagpage for category listings
git = "git" # assume on path
pandoc = "pandoc" # assume on path


def check_pandoc():
    try:
        child = subprocess.Popen([pandoc, "--version"],
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
    except OSError:
        sys.exit("Could not find pandoc on $PATH")
    stdout, stderr = child.communicate()
    if child.returncode:
        sys.exit("Error %i from pandoc version check\n" % child.returncode)
    if not stdout:
        sys.exit("No output from pandoc version check\n")
    for line in stdout.split("\n"):
        if line.startswith("pandoc ") and "." in line:
            print("Will be using " + line)
check_pandoc()


if len(sys.argv) == 1:
    print("Basic Usage: ./convert.py mediawiki.dump")
    print("")
    print('White list: ./convert.py mediawiki.dump "Main Page" "File:Example Image.jpg"')
    sys.exit()

mediawiki_xml_dump = sys.argv[1]  # TODO - proper API
page_whitelist = sys.argv[2:]

missing_users = dict()

assert os.path.isdir(".git"), "Expected to be in a Git repository!"
if prefix:
    assert prefix.endswith("/")
    if not os.path.isdir(prefix):
        os.mkdir(prefix)

user_mapping = dict()
with open(user_table, "r") as handle:
    for line in handle:
        if not line.strip():
            continue
        try:
            username, github = line.strip().split("\t")
        except ValueError:
            sys.stderr.write("Invalid entry in %s: %s" % (user_table, line))
            sys.exit(1)
        # TODO - expand this with a regular expression or something
        if " <" not in github or "@" not in github or ">" not in github:
            sys.stderr.write("Invalid entry for %r: %r\n" % (username, github))
            sys.stderr.write("Second column in %s should use the format: name <email>, e.g.\n" % user_table)
            sys.stderr.write("A.N. Other <a.n.other@example.org>\n")
            sys.exit(1)
        user_mapping[username] = github

blacklist = set()
with open(user_blacklist, "r") as handle:
    for line in handle:
        blacklist.add(line.strip())

if mediawiki_xml_dump.endswith(".gz"):
    xml_handle = gzip.open(mediawiki_xml_dump)
else:
    xml_handle = open(mediawiki_xml_dump)
e = ElementTree.iterparse(xml_handle, events=('start', 'end'))

db = mediawiki_xml_dump + ".sqlite"
if os.path.isfile(db):
    os.remove(db)
conn = sqlite3.connect(db)
c = conn.cursor()
# Going to use this same table for BOTH plain text revisions to pages
# AND for base64 encoded uploads for file attachments, because want
# to sort both by date and turn each into a commit.
c.execute("CREATE TABLE revisions "
          "(title text, filename text, date text, username text, content text, comment text)")

def un_div(text):
    """Remove wrapping <div...>text</div> leaving just text."""
    if text.strip().startswith("<div ") and text.strip().endswith("</div>"):
        text = text.strip()[:-6]
        text = text[text.index(">") + 1:].strip()
    return text

tmp = '<div style="float:left; maxwidth: 180px; margin-left:25px; margin-right:15px; background-color: #FFFFFF">[[Image:Pear.png|left|The Bosc Pear]]</div>'
#print(un_div(tmp))
assert un_div(tmp) == '[[Image:Pear.png|left|The Bosc Pear]]', un_div(tmp)
del tmp

def cleanup_mediawiki(text):
    """Modify mediawiki markup to make it pandoc ready.

    Long term this needs to be highly configurable on a site-by-site
    basis, but for now I'll put local hacks here.

    Returns tuple: cleaned up text, list of any categories
    """
    # This tag was probably setup via SyntaxHighlight GeSHi for biopython.org's wiki
    #
    # <python>
    # import antigravity
    # </python>
    #
    # Replacing it with the following makes pandoc happy,
    #
    # <source lang=python>
    # import antigravity
    # </source>
    #
    # Conversion by pandoc to GitHub Flavour Markdown gives:
    #
    # ``` python
    # import antigravity
    # ```
    #
    # Which is much nicer.
    #
    # =================================================
    #
    # I may have been misled by old links, but right now I don't
    # think there is an easy way to get a table-of-contents with
    # (GitHub Flavoured) Markdown which works on GitHub pages.
    #
    # Meanwhile the MediaWiki __TOC__ etc get left in the .md
    # so I'm just going to remove them here.
    #
    new = []
    categories = []
    languages = ["python", "perl", "sql", "bash", "ruby", "java", "xml"]
    for line in text.split("\n"):
        # line is already unicode
        line = line.replace("\xe2\x80\x8e".decode("utf-8"), "")  # LEFT-TO-RIGHT
        # TODO - Would benefit from state tracking (for tag mismatches)
        for lang in languages:
            # Easy case <python> etc
            if line.lower().startswith("<%s>" % lang):
                line = (("<source lang=%s\n" % lang) + line[len(lang) + 2:]).strip()
            # Also cope with <python id=example> etc:
            elif line.startswith("<%s " % lang) and ">" in line:
                line = (("<source lang=%s " % lang) + line[len(lang) + 2:]).strip()
            # Want to support <python>print("Hello world")</python>
            # where open and closing tags are on the same line:
            if line.rstrip() == "</%s>" % lang:
                line = "</source>"
            elif line.rstrip().endswith("</%s>" % lang):
                line = line.replace("</%s>" % lang, "\n</source>")
        undiv = un_div(line)
        if undiv in ["__TOC__", "__FORCETOC__", "__NOTOC__"]:
            continue
        elif undiv.startswith("[[Image:") and undiv.endswith("]]"):
            # Markdown image wrapped in a div does not render on Github Pages,
            # remove the div and any attempt at styling it (e.g. alignment)
            line = undiv
        # Look for any category tag, usually done as a single line:
        if "[[Category:" in line:
            tag = line[line.index("[[Category:") + 11:]
            tag = tag[:tag.index("]]")]
            assert ("[[Category:%s]]" % tag) in line, "Infered %r from %s" % (tag, line)
            categories.append(tag)
            line = line.replace("[[Category:%s]]" % tag, "").strip()
            if not line:
                continue
        # Special case fix for any category links,
        # See https://github.com/jgm/pandoc/issues/2849
        if "[[:Category:" in line:
            line = line.replace("[[:Category:", "[[Category%3A")
        if "[[User:" in line:
            line = line.replace("[[User:", "[[User%3A")
        new.append(line)
    return "\n".join(new), categories

tmp = '<div style="float:left; maxwidth: 180px; margin-left:25px; margin-right:15px; background-color: #FFF\
FFF">[[Image:Pear.png|left|The Bosc Pear]]</div>'
assert cleanup_mediawiki(tmp) == ('[[Image:Pear.png|left|The Bosc Pear]]', []), cleanup_mediawiki(tmp)
del tmp


def cleanup_markdown(text, source_url):
    """Post-process markdown from pandoc before saving it.

    Currently only want to tweak internal wikilinks which point at
    at (or are from) pages using child namespaces with slashes in them.
    Problem is MediaWiki treats them as absolute (from base path),
    while Jekyll will treat them as relative (to the current path).
    """
    if prefix:
        assert prefix.endswith("/") and source_url.startswith(prefix)
        source = source_url[len(prefix):]
        assert not prefix.startswith("/")
    else:
        source = source_url
    if "/" not in source:
        return text
    if not prefix:
        # How would we change it?
        return text

    # Looking for ...](URL "wikilink")... where the URL should look
    # like a relative link (no http etc), but may not be, e.g.
    # [DAS/1](DAS/1 "wikilink") --> [DAS/1](/wiki/DAS/1 "wikilink")
    p = re.compile(']\([A-Z].* "wikilink"\)')
    for old in p.findall(text):
        if old.startswith(("](http", "](ftp:", "](mailto:")):
            continue
        new = "](/%s%s" % (prefix, old[2:])
        #print("Replacing %s --> %s" % (old[1:], new[1:]))
        text = text.replace(old, new)
    return text


def clean_tag(tag):
    while "}" in tag:
        tag = tag[tag.index("}") + 1:]
    return tag


def make_cannonical(title):
    """Spaces to underscore; first letter upper case only."""
    # Cannot use .title(), e.g. 'Biopython small.jpg' --> 'Biopython Small.Jpg'
    title = title.replace(" ", "_")
    return title[0].upper() + title[1:].lower()

def make_url(title):
    """Spaces to underscore; adds prefix; adds trailing slash."""
    return os.path.join(prefix, title.replace(" ", "_") + "/")

def make_filename(title, ext):
    """Spaces/colons/slahses to underscores; adds extension given.

    Want to avoid colons in filenames for Windows, fix the URL via
    the YAML header with a permalink entry.

    Likewise want to avoid slashes in filenames as causes problems
    with automatic links when there are child-folders. Again we
    get the desired URL via the YAML header permalink entry.
    """
    return os.path.join(prefix, title.replace(" ", "_").replace(":", "_").replace("/", "_") + os.path.extsep + ext)

def ignore_by_prefix(title):
    for prefix in page_prefixes_to_ignore:
        if title.startswith(prefix):
            return True
    return False

def dump_revision(mw_filename, md_filename, text, title):
    # We may have unicode, e.g. character u'\xed' (accented i)
    folder, local_filename = os.path.split(mw_filename)
    original = text
    text, categories = cleanup_mediawiki(text)

    if text.strip().startswith("#REDIRECT [[") and text.strip().endswith("]]"):
        redirect = text.strip()[12:-2]
        if "\n" not in redirect and "]" not in redirect:
            # Maybe I should just have written a regular expression?
            with open(mw_filename, "w") as handle:
                handle.write(original.encode("utf8"))
            with open(md_filename, "w") as handle:
                handle.write("---\n")
                handle.write("title: %s\n" % title.encode("utf-8"))
                handle.write("permalink: %s\n" % make_url(title).encode("utf-8"))
                handle.write("redirect_to: /%s\n" % make_url(redirect).encode("utf-8"))
                handle.write("---\n\n")
                handle.write("You should automatically be redirected to [%s](/%s)\n"
                             % (redirect.encode("utf-8"), make_url(redirect).encode("utf-8")))
            print("Setup redirection %s --> %s" % (title.encode("utf-8"), redirect.encode("utf-8")))
            return True

    with open(mw_filename, "w") as handle:
        handle.write(text.encode("utf8"))
    folder, local_filename = os.path.split(md_filename)
    child = subprocess.Popen([pandoc,
                              "-f", "mediawiki",
                              "-t", "markdown_github-hard_line_breaks",
                              mw_filename],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             )
    stdout, stderr = child.communicate()
    # Now over-write with the original mediawiki to record that in git,
    with open(mw_filename, "w") as handle:
        handle.write(original.encode("utf8"))

    # What did pandoc think?
    if stderr or child.returncode:
        print(stdout)
    if stderr:
        sys.stderr.write(stderr)
    if child.returncode:
        sys.stderr.write("Error %i from pandoc\n" % child.returncode)
    if not stdout:
        sys.stderr.write("No output from pandoc for %r\n" % mw_filename)
    if child.returncode or not stdout:
        return False
    with open(md_filename, "w") as handle:
        handle.write("---\n")
        handle.write("title: %s\n" % title.encode("utf-8"))
        handle.write("permalink: %s\n" % make_url(title).encode("utf-8"))
        if title.startswith("Category:"):
            # This assumes have layout template called tagpage
            # which will insert the tag listing automatically
            # i.e. Behaves like MediaWiki for Category:XXX
            # where we mapped XXX as a tag in Jekyll
            handle.write("layout: tagpage\n")
            handle.write("tag: %s\n" % title[9:])
        else:
            # Note a category page,
            if default_layout:
                handle.write("layout: %s\n" % default_layout)
            if categories:
                # Map them to Jekyll tags as can have more than one per page:
                handle.write("tags:\n")
                for category in categories:
                    handle.write(" - %s\n" % category)
        handle.write("---\n\n")
        handle.write(cleanup_markdown(stdout, make_url(title)))
    return True

def run(cmd_string):
    #print(cmd_string)
    return_code = os.system(cmd_string.encode("utf-8"))
    if return_code:
        sys.stderr.write("Error %i from: %s\n" % (return_code, cmd_string))
        sys.exit(return_code)

def commit_revision(mw_filename, md_filename, username, date, comment):
    assert os.path.isfile(md_filename), md_filename
    assert os.path.isfile(mw_filename), mw_filename
    if not comment:
        comment = "Change to wiki page"
    commit_files([md_filename, mw_filename], username, date, comment)

def commit_files(filenames, username, date, comment):
    assert filenames, "Nothing to commit: %r" % filenames
    for f in filenames:
        assert os.path.isfile(f), f
    cmd = '"%s" add "%s"' % (git, '" "'.join(filenames))
    run(cmd)
    # TODO - how to detect and skip empty commit?
    if username in user_mapping:
        author = user_mapping[username]
    elif username:
        global missing_users
        try:
            missing_users[username] += 1
        except KeyError:
            missing_users[username] = 1
        author = "%s <%s>" % (username, default_email)
    else:
        # git insists on a name, not just an email address:
        author = "Anonymous Contributor <%s>" % default_email
    if not comment:
        comment = "No comment"
    # In order to handle quotes etc in the message, rather than -m "%s"
    # using the -F option and piping to stdin.
    # cmd = '"%s" commit "%s" --date "%s" --author "%s" -m "%s" --allow-empty' \
    #       % (git, filename, date, author, comment)
    cmd = [git, 'commit'] + filenames + [
                              '--date', date,
                              '--author', author,
                              '-F', '-',
                              '--allow-empty']
    child = subprocess.Popen(cmd,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE
                             )
    child.stdin.write(comment.encode("utf8"))
    stdout, stderr = child.communicate()
    if child.returncode or stderr:
        sys.stderr.write(stdout)
    if stderr:
        sys.stderr.write(stderr)
    if child.returncode:
        sys.stderr.write("Return code %i from git commit\n" % child.returncode)
        sys.stderr.write("Popen(%r, ...)\n" % cmd)
        sys.exit(child.returncode)


print("=" * 60)
print("Parsing XML and saving revisions by page.")
usernames = set()
title = None
filename = None
date = None
comment = None
username = None
text = None
for event, element in e:
    tag = clean_tag(element.tag)
    if event == "start":
        if tag == "page":
            assert title is None, title
            assert date is None, date
        if tag == "revision" or tag == "upload":
            assert date is None, "%r for %r" % (date, title)
        if tag == "contents":
            assert element.attrib["encoding"] == "base64"
    elif event == "end":
        if tag == "title":
            title = element.text.strip()
        elif tag == "timestamp":
            date = element.text.strip()
        elif tag == "comment":
            if element.text is not None:
                comment = element.text.strip()
        elif tag == "username":
            username = element.text.strip()
        elif tag == "text":
            text = element.text
        elif tag == "contents":
            # Used in uploads
            text = element.text.strip()
        elif tag == "filename":
            # Expected in uploads
            filename = element.text.strip()
        elif tag == "revision":
            if username is None:
                username = ""
            if comment is None:
                comment = ""
            if username not in blacklist:
                if title.startswith("File:"):
                    #print("Ignoring revision for %s in favour of upload entry" % title)
                    pass
                elif ignore_by_prefix(title):
                    #print("Ignoring revision for %s due to title prefix" % title)
                    pass
                elif text is not None:
                    #print("Recording '%s' as of revision %s by %s" % (title, date, username))
                    c.execute("INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?)",
                              (title, filename, date, username, text, comment))
            filename = date = username = text = comment = None
        elif tag == "upload":
            assert title.startswith("File:")
            # Want to treat like a revision?
            if username is None:
                username = ""
            if comment is None:
                comment = ""
            if username not in blacklist:
                if text is not None or title.startswith("File:"):
                    #print("Recording '%s' as of upload %s by %s" % (title, date, username))
                    c.execute("INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?)",
                              (title, filename, date, username, text, comment))
            filename = date = username = text = comment = None
        elif tag == "page":
            assert date is None, date
            title = filename = date = username = text = comment = None
    else:
        sys.exit("Unexpected event %r with element %r" % (event, element))
xml_handle.close()


def commit_file(title, filename, date, username, contents, comment):
    # commit an image or other file from its base64 encoded representation
    assert title.startswith("File:")
    if not filename:
        filename = os.path.join(prefix, make_cannonical(title[5:]))  # should already have extension
    print("Committing %s as of upload %s by %s" % (filename, date, username))
    with open(filename, "wb") as handle:
        handle.write(base64.b64decode(contents))
    commit_files([filename], username, date, comment)


if sys.platform != "linux2":
    #print("=" * 60)
    #print("Checking for potential name clashes")
    names = dict()
    for title, in c.execute('SELECT DISTINCT title FROM revisions ORDER BY title'):
        if ignore_by_prefix(title):
            assert False, "Should have already excluded %s?" % title
            pass
        elif title.lower() not in names:
            names[title.lower()] = title
        else:
            if names[title.lower()] != title:
                print("WARNING: Multiple case variants exist, e.g.")
                print(" - " + title)
                print(" - " + names[title.lower()])
                print("If your file system cannot support such filenames at the same time")
                print("(e.g. Windows, or default Mac OS X) this conversion will FAIL.")
                break
print("=" * 60)
print("Sorting changes by revision date...")
for title, filename, date, username, text, comment in c.execute('SELECT * FROM revisions ORDER BY date, title'):
    if filename:
        filename = os.path.join(prefix, filename)
    if text is None:
        assert title.startswith("File:"), date
    # assert text is not None, date
    if page_whitelist and title not in page_whitelist:
        # Not wanted, ignore
        # print("Ignoring: %s" % title)
        continue
    if ignore_by_prefix(title):
        # Not interesting, ignore
        continue
    if title.startswith("File:"):
        # Example Title File:Wininst.png
        # TODO - capture the preferred filename from the XML!
        commit_file(title, filename, date, username, text, comment)
        continue
    if title.startswith("Template:"):
        # Can't handle these properly (yet)
        continue
    # if title.startswith("Category:"):
    #     # TODO - may need to insert some Jekyll template magic?
    #     # See https://github.com/peterjc/mediawiki_to_git_md/issues/6
    assert filename is None
    md_filename = make_filename(title, markdown_ext)
    mw_filename = make_filename(title, mediawiki_ext)
    print("Converting %s as of revision %s by %s" % (md_filename, date, username))
    if dump_revision(mw_filename, md_filename, text, title):
        commit_revision(mw_filename, md_filename, username, date, comment)
    else:
        # Only the mediawiki changed, could not convert to markdown.
        cmd = "git reset --hard"
        run(cmd)
        sys.stderr.write("Skipping this revision!\n")

print("=" * 60)
if missing_users:
    print("Missing information for these usernames:")
    for username in sorted(missing_users):
        print("%i - %s" % (missing_users[username], username))

print("Removing any empty commits...")
run("%s filter-branch --prune-empty -f HEAD" % git)
print("Done")
