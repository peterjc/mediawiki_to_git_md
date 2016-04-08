#!/usr/bin/env python
import os
import sys
import subprocess
import sqlite3
import base64
from xml.etree import cElementTree as ElementTree

if len(sys.argv) == 1:
    print("Basic Usage: ./convert.py mediawiki.dump")
    print("")
    print('White list: ./convert.py mediawiki.dump "Main Page" "File:Example Image.jpg"')
    sys.exit()

mediawiki_xml_dump = sys.argv[1]  # TODO - proper API
page_whitelist = sys.argv[2:]

prefix = "wiki/"
mediawiki_ext = "mediawiki"
markdown_ext = "md"
user_table = "usernames.txt"
user_blacklist = "user_blocklist.txt"
default_email = "anonymous.contributor@example.org"
base_url = "http://www.open-bio.org/" # Used for images etc; prefix is appended to this!
base_image_url = base_url + "w/images/" # Used for images


git = "git" # assume on path
pandoc = "pandoc" # assume on path

missing_users = dict()

assert os.path.isdir(".git"), "Expected to be in a Git repository!"

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

e = ElementTree.iterparse(open(mediawiki_xml_dump), events=('start', 'end'))

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

def sys_exit(msg, error_level=1):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.exit(error_level)

def un_div(text):
    """Remove wrapping <div...>text</div> leaving just text."""
    if text.strip().startswith("<div ") and text.strip().endswith("</div>"):
        text = text.strip()[:-6]
        text = text[text.index(">") + 1:].strip()
    return text

print  un_div('<div style="float:left; maxwidth: 180px; margin-left:25px; margin-right:15px; background-color: #FFFFFF">[[Image:Pear.png|left|The Bosc Pear]]</div>')
assert un_div('<div style="float:left; maxwidth: 180px; margin-left:25px; margin-right:15px; background-color: #FFFFFF">[[Image:Pear.png|left|The Bosc Pear]]</div>') == '[[Image:Pear.png|left|The Bosc Pear]]'

def cleanup_mediawiki(text):
    """Modify mediawiki markup to make it pandoc ready.

    Long term this needs to be highly configurable on a site-by-site
    basis, but for now I'll put local hacks here.
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
    for line in text.split("\n"):
        # line is already unicode
        line = line.replace("\xe2\x80\x8e".decode("utf-8"), "")  # LEFT-TO-RIGHT
        if line.rstrip() == "<python>":
            line = "<source lang=python>"
        elif line.rstrip() == "<perl>":
            line = "<source lang=perl>"
        elif line.rstrip() in ["</python>", "</perl>"]:
            line = "</source>"
        undiv = un_div(line)
        if undiv in ["__TOC__", "__FORCETOC__", "__NOTOC__"]:
            continue
        elif undiv.startswith("[[Image:") and undiv.endswith("]]"):
            # Markdown image wrapped in a div does not render on Github Pages,
            # remove the div and any attempt at styling it (e.g. alignment)
            line = undiv
        new.append(line)
    return "\n".join(new)

assert cleanup_mediawiki('<div style="float:left; maxwidth: 180px; margin-left:25px; margin-right:15px; background-color: #FFFFFF">[[Image:Pear.png|left|The Bosc Pear]]</div>') == '[[Image:Pear.png|left|The Bosc Pear]]'

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
    """Spaces to underscore; adds prefix."""
    return os.path.join(prefix, title.replace(" ", "_"))

def make_filename(title, ext):
    """Spaces to underscore; addsplus prefix and extension given."""
    return make_url(title) + os.path.extsep + ext

def mkdir_recursive(path):
    paths = [x for x in os.path.split(path) if x]
    for i in range(len(paths)):
        p = os.path.join(*paths[:i+1])
        #print("*** %r -> %r" % (paths, p))
        if not os.path.exists(p):
            os.mkdir(p)
    assert os.path.exists(path)

def dump_revision(mw_filename, md_filename, text, title):
    # We may have unicode, e.g. character u'\xed' (accented i)
    # Make folder in case have example like 'wiki/BioSQL/Windows.md

    folder, local_filename = os.path.split(mw_filename)
    mkdir_recursive(folder)
    with open(mw_filename, "w") as handle:
        handle.write(cleanup_mediawiki(text).encode("utf8"))

    if text.strip().startswith("#REDIRECT [[") and text.strip().endswith("]]"):
        redirect = text.strip()[12:-2]
        if "\n" not in redirect and "]" not in redirect:
            # Maybe I should just have written a regular expression?
            with open(md_filename, "w") as handle:
                handle.write("---\n")
                handle.write("title: %s\n" % title)
                handle.write("redirect_to: /%s\n" % make_url(redirect))
                handle.write("---\n\n")
                handle.write("You should automatically be redirected to [%s](/%s)\n"
                             % (redirect, make_url(redirect)))
            print("Setup redirection %s --> %s" % (title, redirect))
            return True

    folder, local_filename = os.path.split(md_filename)
    mkdir_recursive(folder)
    child = subprocess.Popen([pandoc,
                              "-f", "mediawiki",
                              "-t", "markdown_github-hard_line_breaks",
                              mw_filename],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             )
    stdout, stderr = child.communicate()
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
        handle.write("title: %s\n" % title)
        handle.write("---\n\n")
        handle.write(stdout)
    return True

def run(cmd_string):
    #print(cmd_string)
    return_code = os.system(cmd_string)
    if return_code:
        sys_exit("Error %i from: %s" % (return_code, cmd_string), return_code)

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
        author = "Anonymous Contributor <%s> % default_email"
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
        sys_exit("Unexpected event %r with element %r" % (event, element))

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
        if title.lower() not in names:
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
    if title.startswith("MediaWiki:") or title.startswith("Help:"):
        # Not interesting, ignore
        continue
    if title.startswith("File:"):
        # Example Title File:Wininst.png
        # TODO - capture the preferred filename from the XML!
        commit_file(title, filename, date, username, text, comment)
        continue
    if title.startswith("User:") or title.startswith("Talk:") or title.startswith("User_talk:"):
        # Not wanted, ignore
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

