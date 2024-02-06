#!/usr/bin/env python
import argparse
import os
import sys
import subprocess
import sqlite3
import base64
import re
from xml.etree import cElementTree as ElementTree

# User configurable bits (ought to be command line options?):

debug = False

__version__ = "2.0.0.dev0"

if "-v" in sys.argv or "--version" in sys.argv:
    print("This is mediawiki_to_git_md script xml_to_git.py version " + __version__)
    sys.exit(0)

if len(sys.argv) == 1:
    print("This is mediawiki_to_git_md script xml_to_git.py version " + __version__)
    print("")
    print("Basic Usage: ./xml_to_git.py -i mediawiki.dump")
    print("")
    print(
        'White list: ./xml_to_git.py -i mediawiki.dump -t "Main Page" "File:Example Image.jpg"'
    )
    sys.exit()

usage = """\
Run this script in a git repository where it will make commits to the
current branch based on parsing a MediaWiki XML dump. e.g.

$ git tag start
$ git checkout -b import_branch
$ python xml_to_git.py -i ../dump.xml

Tagging the repository before starting and/or making a branch makes it
easy to revert. As of v2, this records the revisions in the original
MediaWiki markup (plus header with original title and URL), with the
expectation of final commits using mediawiki_to_md.py and pandoc.
"""

parser = argparse.ArgumentParser(
    prog="xml_to_git.py",
    description="Turn a MediaWiki XML dump into MediaWiki commits in git",
    epilog=usage,
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
parser.add_argument(
    "-i",
    "--input",
    metavar="XML",
    required=True,
    help="MediaWiki XML file, can be gzip or bz compressed.",
)
parser.add_argument(
    "-t",
    "--titles",
    metavar="TITLE",
    nargs="+",
    help="Optional white-list of page tiles to import (rest ignored).",
)
parser.add_argument(
    "-u",
    "--usernames",
    metavar="FILENAME",
    default="usernames.txt",
    help="Simple two-column TSV file mapping MediaWiki usernames to git "
    "author entries like 'name <email@example.org>'. Default 'usernames.txt'",
)
parser.add_argument(
    "-b",
    "--blocklist",
    metavar="FILENAME",
    default="user_blocklist.txt",
    help="Simple text file file of MediaWiki usernames (spammers etc). "
    "Uploads will be ignored, but revisions will be recorded with the "
    "comment 'UNWANTED FROM <Username>' allowing history editing later. "
    "Default 'user_blocklist.txt''.",
)
parser.add_argument(
    "-e",
    "--default-email",
    metavar="EMAIL",
    default="anonymous.contributor@example.org",
    help="Email address for users not in the mapping, "
    "default 'anonymous.contributor@example.org'.",
)
parser.add_argument(
    "-p",
    "--prefix",
    metavar="PREFIX",
    default="wiki/",
    help="URL prefix and subfolder, default 'wiki/'.",
)
parser.add_argument(
    "--mediawiki-ext",
    metavar="EXT",
    default="mediawiki",
    help="File extension for MediaWiki files, default 'mediawiki'.",
)

args = parser.parse_args()

mediawiki_xml_dump = args.input
page_whitelist = args.titles
prefix = args.prefix
mediawiki_ext = args.mediawiki_ext
user_table = args.usernames
user_blocklist = args.blocklist
default_email = args.default_email

# Do these need to be configurable?:
page_prefixes_to_ignore = [
    "Help:",
    "MediaWiki:",
    "Talk:",
    "User:",
    "User talk:",
]  # Beware spaces vs _
default_layout = "wiki"  # Can also use None; note get tagpage for category listings
git = "git"  # assume on path


missing_users = dict()
unwanted_commits = 0


assert os.path.isdir(".git"), "Expected to be in a Git repository!"
if prefix:
    assert prefix.endswith("/")
    if not os.path.isdir(prefix):
        os.mkdir(prefix)

user_mapping = dict()
if os.path.isfile(user_table):
    sys.stderr.write(f"Loading {user_table}\n")
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
                sys.stderr.write(
                    "Second column in %s should use the format: name <email>, e.g.\n"
                    % user_table
                )
                sys.stderr.write("A.N. Other <a.n.other@example.org>\n")
                sys.exit(1)
            user_mapping[username] = github
else:
    sys.stderr.write("WARNING - running without username to GitHub mapping\n")

blocklist = set()
if os.path.isfile(user_blocklist):
    sys.stderr.write(f"Loading {user_blocklist}\n")
    with open(user_blocklist, "r") as handle:
        for line in handle:
            blocklist.add(line.strip())
else:
    sys.stderr.write("WARNING - running without username ignore list\n")


def make_cannonical(title):
    """Spaces to underscore; first letter upper case only."""
    # Cannot use .title(), e.g. 'Biopython small.jpg' --> 'Biopython Small.Jpg'
    title = title.replace(" ", "_")
    return title[0].upper() + title[1:].lower()


def make_filename(title, ext):
    """Spaces/colons/slahses to underscores; adds extension given.

    Want to avoid colons in filenames for Windows, fix the URL via
    the YAML header with a permalink entry.

    Likewise want to avoid slashes in filenames as causes problems
    with automatic links when there are child-folders. Again we
    get the desired URL via the YAML header permalink entry.
    """
    return os.path.join(
        prefix,
        title.replace(" ", "_").replace(":", "_").replace("/", "_")
        + os.path.extsep
        + ext,
    )


def ignore_by_prefix(title):
    for prefix in page_prefixes_to_ignore:
        if title.startswith(prefix):
            return True
    return False


def runsafe(cmd_array):
    args = []
    for el in cmd_array:
        args.append(el.encode("utf-8"))
    return_code = subprocess.call(args)
    if return_code:
        sys.stderr.write("Error %i from: %s\n" % (return_code, " ".join(cmd_array)))
        sys.exit(return_code)


def commit_files(filenames, username, date, comment):
    assert filenames, "Nothing to commit: %r" % filenames
    for f in filenames:
        assert f and os.path.isfile(f), f
    cmd = [git, "add"] + filenames
    runsafe(cmd)
    # TODO - how to detect and skip empty commit?
    if username in user_mapping:
        author = user_mapping[username]
    elif username in blocklist:
        author = "Unwanted Contributor %s <%s>" % (username, default_email)
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
    cmd = (
        [git, "commit"]
        + filenames
        + ["--date", date, "--author", author, "-F", "-", "--allow-empty"]
    )
    child = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    child.stdin.write(comment.encode("utf8"))
    stdout, stderr = child.communicate()
    if child.returncode or stderr:
        sys.stderr.write(stdout.decode("utf8"))
    if stderr:
        sys.stderr.write(stderr.decode("utf8"))
    if child.returncode:
        sys.stderr.write("Return code %i from git commit\n" % child.returncode)
        sys.stderr.write("Popen(%r, ...)\n" % cmd)
        sys.exit(child.returncode)


def parse_xml(mediawiki_xml_dump):
    print("=" * 60)
    print("Parsing XML and saving revisions by page.")

    if mediawiki_xml_dump in ["-", "/dev/stdin"]:
        xml_handle = open("/dev/stdin", "rb")
    elif mediawiki_xml_dump.endswith(".gz"):
        import gzip

        xml_handle = gzip.open(mediawiki_xml_dump, "rb")
    elif mediawiki_xml_dump.endswith(".bz2"):
        import bz2

        xml_handle = bz2.open(mediawiki_xml_dump, "rb")
    else:
        xml_handle = open(mediawiki_xml_dump, "rb")

    usernames = set()
    title = None
    filename = None
    date = None
    comment = None
    username = None
    text = None
    revision_count = 0
    e = ElementTree.iterparse(xml_handle, events=("start", "end"))
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
                if title.startswith("File:"):
                    # print("Ignoring revision for %s in favour of upload entry" % title)
                    pass
                elif ignore_by_prefix(title):
                    # print("Ignoring revision for %s due to title prefix" % title)
                    pass
                elif text is not None:
                    # if debug:
                    #     sys.stderr.write(f"Recording '{title}' as of {date} by {username}\n")
                    c.execute(
                        "INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?)",
                        (title, filename, date, username, text, comment),
                    )
                    revision_count += 1
                    if revision_count % 10000 == 0:
                        sys.stderr.write(f"DEBUG: {revision_count} revisions so far\n")
                        conn.commit()
                    if debug and revision_count > 500:
                        sys.stderr.write("DEBUG: That's enough for testing now!\n")
                        break
                filename = date = username = text = comment = None
            elif tag == "upload":
                assert title.startswith("File:")
                # Want to treat like a revision?
                if username is None:
                    username = ""
                if comment is None:
                    comment = ""
                if text is not None or title.startswith("File:"):
                    # print("Recording '%s' as of upload %s by %s" % (title, date, username))
                    c.execute(
                        "INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?)",
                        (title, filename, date, username, text, comment),
                    )
                filename = date = username = text = comment = None
            elif tag == "page":
                assert date is None, date
                title = filename = date = username = text = comment = None
        else:
            sys.exit("Unexpected event %r with element %r" % (event, element))
    xml_handle.close()
    print("Finished parsing XML and saved revisions by page.")
    conn.commit()


db = mediawiki_xml_dump + ".sqlite"
if mediawiki_xml_dump in ["-", "/dev/stdin"]:
    db = "stdin.sqlite"

if (
    db != "stdin.sqlite"
    and os.path.isfile(db)
    and os.stat(mediawiki_xml_dump).st_mtime < os.stat(db).st_mtime
):
    sys.stderr.write(f"Checking SQLite file {db}\n")
    conn = sqlite3.connect(db)
    c = conn.cursor()
    (count,) = c.execute("SELECT COUNT(*) FROM revisions;").fetchone()
    if not count:
        sys.exit(f"SQLite file {db} has no revisions\n")
    sys.stderr.write(f"SQLite file {db} has {count} revisions\n")
    (count,) = c.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
        "AND tbl_name='revisions' and name='idx_date_title';"
    ).fetchone()
    if not count:
        sys.exit(f"Can't reuse partial SQLite file {db} - missing index")

else:
    sys.stderr.write(f"Creating SQLite file {db}\n")
    if os.path.isfile(db):
        os.remove(db)
    assert db != db.upper()
    if os.path.isfile(db.upper()):
        os.remove(db.upper())

    conn = sqlite3.connect(db)
    c = conn.cursor()
    # Going to use this same table for BOTH plain text revisions to pages
    # AND for base64 encoded uploads for file attachments, because want
    # to sort both by date and turn each into a commit.
    c.execute(
        "CREATE TABLE revisions "
        "(title text, filename text, date text, username text, content text, comment text)"
    )
    parse_xml(mediawiki_xml_dump)
    c.execute("CREATE INDEX idx_date_title ON revisions(date, title);")
    conn.commit()
    sys.stderr.write(f"Created SQLite file {db}\n")


def commit_file(title, filename, date, username, contents, comment):
    # commit an image or other file from its base64 encoded representation
    assert username not in blocklist
    assert title.startswith("File:")
    if not filename:
        filename = os.path.join(
            prefix, make_cannonical(title[5:])
        )  # should already have extension
    print("Commit %s %s by %s : %s" % (date, filename, username, comment[:40]))
    with open(filename, "wb") as handle:
        handle.write(base64.b64decode(contents))
    commit_files([filename], username, date, comment)


CASE_SENSITIVE = False
try:
    os.lstat(db.upper())
except IOError as e:
    import errno

    if e.errno == errno.ENOENT:
        CASE_SENSITIVE = True
if not CASE_SENSITIVE:
    sys.stderr.write("WARNING: File system is case insensitive - a potential issue.\n")
    # print("=" * 60)
    # print("Checking for potential name clashes")
    names = dict()
    # This will be slow with a large DB!
    for (title,) in c.execute("SELECT DISTINCT title FROM revisions ORDER BY title"):
        if page_whitelist and title not in page_whitelist:
            continue
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
                print(
                    "If your file system cannot support such filenames at the same time"
                )
                print("(e.g. Windows, or default Mac OS X) this conversion will FAIL.")
                # sys.exit(
                #    "ERROR: Mixed case files found, but file system insensitive"
                # )  # needs a --force option or something?

print("=" * 60)
print("Sorting changes by revision date...")
for title, filename, date, username, text, comment in c.execute(
    "SELECT * FROM revisions ORDER BY date, title"
):
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
        if username in blocklist:
            sys.stderr.write(f"Ignoring upload {filename} from {username}\n")
        commit_file(title, filename, date, username, text, comment)
        continue
    if title.startswith("Template:"):
        # Can't handle these properly (yet)
        continue
    # if title.startswith("Category:"):
    #     # TODO - may need to insert some Jekyll template magic?
    #     # See https://github.com/peterjc/mediawiki_to_git_md/issues/6
    assert filename is None
    mw_filename = make_filename(title, mediawiki_ext)
    if username in blocklist:
        unwanted_commits += 1
        comment = f"UNWANTED FROM {username}"
        print(f"UNWANTED {date} {mw_filename} by {username}")
    else:
        print(f"Commit {date} {mw_filename} by {username}")
    if not comment:
        comment = f"Update {title}"
    with open(mw_filename, "w") as handle:
        # We need to record the page title somewhere
        # Might as well use a Markdown style header block:
        handle.write("---\n")
        handle.write("title: %s\n" % title)
        handle.write("---\n\n")
        handle.write(text)
    commit_files([mw_filename], username, date, comment)

print("=" * 60)
if missing_users:
    print("Missing information for these usernames:")
    for username in sorted(missing_users):
        print("%i - %s" % (missing_users[username], username))

print(f"There are {unwanted_commits} unwanted commits from blocked users.")
print("Done")
