#!/usr/bin/env python
import os
import sys
import subprocess
import sqlite3
from xml.etree import cElementTree as ElementTree

mediawiki_xml_dump = sys.argv[1]  # TODO - proper API
prefix = "wiki/"
mediawiki_ext = "mediawiki"
markdown_ext = "md"
user_table = "usernames.txt"
user_blacklist = "user_blacklist.txt"
default_email = "anonymous.contributor@example.org"


git = "git" # assume on path
pandoc = "pandoc" # assume on path

missing_users = dict()

assert os.path.isdir(".git"), "Expected to be in a Git repository!"

user_mapping = dict()
with open(user_table, "r") as handle:
    for line in handle:
        username, github = line.strip().split("\t")
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
c.execute("CREATE TABLE revisions "
          "(title text, date text, username text, content text, comment text)")

def sys_exit(msg, error_level=1):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.exit(error_level)

def clean_tag(tag):
    while "}" in tag:
        tag = tag[tag.index("}") + 1:]
    return tag

def make_filename(title, ext):
    filename = title.replace(" ", "_")
    return os.path.join(prefix, filename + os.path.extsep + ext)

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
        handle.write(text.encode("utf8"))

    folder, local_filename = os.path.split(md_filename)
    mkdir_recursive(folder)
    child = subprocess.Popen([pandoc,
                              "-f", "mediawiki",
                              "-t", "markdown_github",
                              mw_filename],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             )
    stdout, stderr = child.communicate()
    if stderr:
        print stderr
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
    print(cmd_string)
    return_code = os.system(cmd_string)
    if return_code:
        sys_exit("Error %i from: %s" % (return_code, cmd_string), return_code)

def commit_revision(mw_filename, md_filename, username, date, comment):
    assert os.path.isfile(md_filename), md_filename
    assert os.path.isfile(mw_filename), mw_filename
    cmd = '"%s" add "%s" "%s"' % (git, md_filename, mw_filename)
    run(cmd)
    if not comment:
        comment = "Change to wiki page"
    # TODO - how to detect and skip empty commit?
    if username in user_mapping:
        author = user_mapping[username]
    else:
        global missing_users
        try:
            missing_users[username] += 1
        except KeyError:
            missing_users[username] = 1
        author = "%s <%s>" % (username, default_email)
    # In order to handle quotes etc in the message, rather than -m "%s"
    # using the -F option and piping to stdin.
    # cmd = '"%s" commit "%s" --date "%s" --author "%s" -m "%s" --allow-empty' \
    #       % (git, filename, date, author, comment)
    child = subprocess.Popen([git, 'commit', mw_filename, md_filename,
                              '--date', date,
                              '--author', author,
                              '-F', '-',
                              '--allow-empty'],
                             stdin=subprocess.PIPE,
                             )
    child.stdin.write(comment.encode("utf8"))
    stdout, stderr = child.communicate()
    if stderr:
        print stderr


print("=" * 60)
print("Parsing XML and saving revisions by page.")
usernames = set()
title = None
date = None
comment = None
username = None
text = None
for event, element in e:
    tag = clean_tag(element.tag)
    if event == "start":
        if tag == "page":
            assert title is None
        if tag == "revision":
            assert date is None
    elif event == "end":
        if tag == "title":
            title = element.text.strip()
        elif tag == "timestamp":
            date = element.text.strip()
        elif tag == "comment":
            comment = element.text.strip()
        elif tag == "username":
            username = element.text.strip()
        elif tag == "text":
            text = element.text
        elif tag == "revision":
            if username is None:
                username = ""
            if comment is None:
                comment = ""
            if text is not None and username not in blacklist:
                #print("Recording '%s' as of revision %s by %s" % (title, date, username))
                assert text is not None, date
                c.execute("INSERT INTO revisions VALUES (?, ?, ?, ?, ?)",
                          (title, date, username, text, comment))
            date = username = text = comment = None
        elif tag == "page":
            assert date is None
            title = date = username = text = comment = None
    else:
        sys_exit("Unexpected event %r with element %r" % (event, element))

print("=" * 60)
print("Sorting changes by revision date...")
for title, date, username, text, comment in c.execute('SELECT * FROM revisions ORDER BY date, title'):
    assert text is not None, date
    if title.startswith("MediaWiki:") or title.startswith("Help:"):
        # Not interesting, ignore
        continue
    if title.startswith("File:"):
        # TODO - capture the actuall file rather than the wiki page about the file
        continue
    if title.startswith("User:") or title.startswith("Talk:") or title.startswith("User_talk:"):
        # Not wanted, ignore
        continue
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

