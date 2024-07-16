#!/usr/bin/env python3
import argparse
import glob
import os
import re
import sys
import subprocess
import tempfile

# User configurable bits (ought to be command line options?):

debug = False

__version__ = "2.0.1"

if "-v" in sys.argv or "--version" in sys.argv:
    print("This is mediawiki_to_git_md script mediawiki_to_md version " + __version__)
    sys.exit(0)

if len(sys.argv) == 1:
    print("This is mediawiki_to_git_md script mediawiki_to_md version " + __version__)
    print("")
    print("Basic Usage: ./mediawiki_to_md .")
    print("")
    sys.exit()

usage = """\
Run this script in a git repository where it will make commits to the
current branch based on having already parsed a MediaWiki XML dump. e.g.

$ git tag start
$ git checkout -b import_branch
$ python xml_to_git.py -i ../dump.xml

Then:

$ python mediawiki_to_md.py -i .

Tagging the repository before starting and/or making a branch makes it
easy to revert. As of v2, this records the revisions in the original
MediaWiki markup, with this script handling final commits converting the
final version into Markdown using Pandoc.
"""

parser = argparse.ArgumentParser(
    prog="mediawiki_to_md.py",
    description="Turn set of MediaWiki files into Markdown for GitHub Pages",
    epilog=usage,
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
parser.add_argument(
    "-i",
    "--input",
    metavar="NAMES",
    nargs="+",
    required=True,
    help="MediaWiki filenames and/or foldernames within the current git repository.",
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
parser.add_argument(
    "--markdown-ext",
    metavar="EXT",
    default="md",
    help="File extension for MarkDown files, default 'md'.",
)


args = parser.parse_args()

prefix = args.prefix
mediawiki_ext = args.mediawiki_ext
markdown_ext = args.markdown_ext

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
pandoc = "pandoc"  # assume on path


def check_pandoc():
    try:
        child = subprocess.Popen(
            [pandoc, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
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


missing_users = dict()
unwanted_commits = 0


assert os.path.isdir(".git"), "Expected to be in a Git repository!"
if prefix:
    assert prefix.endswith("/")
    if not os.path.isdir(prefix):
        os.mkdir(prefix)


def un_div(text):
    """Remove wrapping <div...>text</div> leaving just text."""
    if text.strip().startswith("<div ") and text.strip().endswith("</div>"):
        text = text.strip()[:-6]
        text = text[text.index(">") + 1 :].strip()
    return text


tmp = '<div style="float:left; maxwidth: 180px; margin-left:25px; margin-right:15px; background-color: #FFFFFF">[[Image:Pear.png|left|The Bosc Pear]]</div>'
# print(un_div(tmp))
assert un_div(tmp) == "[[Image:Pear.png|left|The Bosc Pear]]", un_div(tmp)
del tmp


def cleanup_mediawiki(text):
    """Modify mediawiki markup to make it pandoc ready.

    Long term this needs to be highly configurable on a site-by-site
    basis, but for now I'll put local hacks here.

    Returns tuple: cleaned up text, list of any categories, title
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

    # This is fragile, but good enough
    if not text.startswith("---\ntitle: "):
        sys.exit("ERROR: Missing our title header")
    text = text[10:].strip()
    title, text = text.split("\n", 1)
    assert text.startswith("---\n")
    text = text[4:]

    for line in text.split("\n"):
        # line is already unicode
        # TODO - line = line.replace("\xe2\x80\x8e".decode("utf-8"), "")  # LEFT-TO-RIGHT
        # TODO - Would benefit from state tracking (for tag mismatches)
        for lang in languages:
            # Easy case <python> etc
            if line.lower().startswith("<%s>" % lang):
                line = (("<source lang=%s\n" % lang) + line[len(lang) + 2 :]).strip()
            # Also cope with <python id=example> etc:
            elif line.startswith("<%s " % lang) and ">" in line:
                line = (("<source lang=%s " % lang) + line[len(lang) + 2 :]).strip()
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
        while "[[Category:" in line:
            tag = line[line.index("[[Category:") + 11 :]
            tag = tag[: tag.index("]]")]
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
    return "\n".join(new), categories, title


tmp = """\
---
title: Test
---
<div style="float:left; maxwidth: 180px; margin-left:25px; margin-right:15px; background-color: #FFF\
FFF">[[Image:Pear.png|left|The Bosc Pear]]</div>"""
assert cleanup_mediawiki(tmp) == (
    "[[Image:Pear.png|left|The Bosc Pear]]",
    [],
    "Test",
), cleanup_mediawiki(tmp)
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
        source = source_url[len(prefix) :]
        assert not prefix.startswith("/")
    else:
        source = source_url
    if "/" not in source:
        return text
    base, page = source.rsplit("/", 1)

    # Looking for ...](URL "wikilink")... where the URL should look
    # like a relative link (no http etc)
    p = re.compile(']\([A-Z].* "wikilink"\)')
    for old in p.findall(text):
        if old.startswith(("](https:", "](http:", "](ftp:", "](mailto:", "])/")):
            continue
        new = "](%s" % os.path.relpath(old[2:], base)
        # print("Replacing %s --> %s" % (old[1:], new[1:]))
        text = text.replace(old, new)
    return text


def clean_tag(tag):
    while "}" in tag:
        tag = tag[tag.index("}") + 1 :]
    return tag


def make_cannonical(title):
    """Spaces to underscore; first letter upper case only."""
    # Cannot use .title(), e.g. 'Biopython small.jpg' --> 'Biopython Small.Jpg'
    title = title.replace(" ", "_")
    return title[0].upper() + title[1:].lower()


def make_url(title):
    """Spaces to underscore; adds prefix; no trailing slash."""
    return os.path.join(prefix, title.replace(" ", "_").replace(":", "%3A"))


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


def run(cmd_string):
    # print(cmd_string)
    return_code = os.system(cmd_string.encode("utf-8"))
    if return_code:
        sys.stderr.write("Error %i from: %s\n" % (return_code, cmd_string))
        sys.exit(return_code)


def runsafe(cmd_array):
    args = []
    for el in cmd_array:
        args.append(el.encode("utf-8"))
    return_code = subprocess.call(args)
    if return_code:
        sys.stderr.write("Error %i from: %s\n" % (return_code, " ".join(cmd_array)))
        sys.exit(return_code)


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


names = []
for name in args.input:
    if name.startswith("../"):
        sys.exit(
            f"ERROR: Input files must be within the current directory and git repo"
        )
    if os.path.isdir(name):
        names.extend(glob.glob(name + "/*." + mediawiki_ext))
    elif os.path.isfile(name) and name.endswith("." + mediawiki_ext):
        names.append(name)
    else:
        sys.exit(f"ERROR: Unexpected input {name}")
print(f"Have {len(names)} input MediaWiki files")

print("Checking for redirects...")
redirects = {}
redirects_from = {}
for mw_filename in names:
    with open(mw_filename) as handle:
        original = handle.read()

    assert original.startswith("---\ntitle: "), mw_filename
    text, categories, title = cleanup_mediawiki(original)

    if text.strip().startswith("#REDIRECT [[") and text.strip().endswith("]]"):
        # Internal redirect, will become a redirect_from entry in target page
        redirect = text.strip()[12:-2]
        if "\n" not in redirect and "]" not in redirect:
            # Maybe I should just have written a regular expression?
            # We will do these AFTER converting the target using redirect_from
            print(f" * redirection {mw_filename} --> {redirect}")
            redirects[mw_filename] = redirect
            try:
                redirects_from[redirect].append(title)
            except KeyError:
                redirects_from[redirect] = [title]
    elif text.strip().startswith("{{#externalredirect:") and text.strip().endswith(
        "}}"
    ):
        # External redirect
        redirect = text.strip()[21:-2].strip()
        redirects[mw_filename] = redirect
        print(f" * redirection {mw_filename} --> {redirect}")
        md_filename = mw_filename[: -len(mediawiki_ext)] + markdown_ext
        if os.path.isfile(md_filename):
            sys.stderr.write(f"WARNING - will overwrite {md_filename}\n")
        with open(md_filename, "w") as handle:
            handle.write("---\n")
            handle.write("title: %s\n" % title)
            handle.write("permalink: %s\n" % make_url(title))
            handle.write(f"redirect_to: {redirect}\n")
            handle.write("---\n")
            handle.write("\n")
            handle.write(f"You should be redirected to <{redirect}>\n")


print("Converting pages...")
for mw_filename in names:
    if mw_filename in redirects:
        continue
    md_filename = mw_filename[: -len(mediawiki_ext)] + markdown_ext
    if os.path.isfile(md_filename):
        sys.stderr.write(f"WARNING - will overwrite {md_filename}\n")

    print(f" * {mw_filename} --> {md_filename}")

    # Yes, sadly we've opened most files twice :(
    with open(mw_filename) as handle:
        original = handle.read()

    assert original.startswith("---\ntitle: "), mw_filename
    text, categories, title = cleanup_mediawiki(original)

    with tempfile.NamedTemporaryFile("w", delete=False) as handle:
        handle.write(text)
        tmp_mediawiki = handle.name

    # TODO - Try piping text via stdin
    folder, local_filename = os.path.split(md_filename)
    child = subprocess.Popen(
        [
            pandoc,
            "-f",
            "mediawiki",
            "-t",
            # "markdown_github-hard_line_breaks",
            "gfm-hard_line_breaks",
            tmp_mediawiki,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = child.communicate()

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
        sys.exit("ERROR - Calling pandoc failed")
    with open(md_filename, "w") as handle:
        handle.write("---\n")
        handle.write("title: %s\n" % title)
        handle.write("permalink: %s\n" % make_url(title))
        if title.startswith("Category:"):
            # This assumes have layout template called tagpage
            # which will insert the tag listing automatically
            # i.e. Behaves like MediaWiki for Category:XXX
            # where we mapped XXX as a tag in Jekyll
            handle.write("layout: tagpage\n")
            handle.write("tag: %s\n" % title[9:])
        else:
            # Not a category page,
            if default_layout:
                handle.write("layout: %s\n" % default_layout)
            if categories:
                # Map them to Jekyll tags as can have more than one per page:
                handle.write("tags:\n")
                for category in categories:
                    handle.write(" - %s\n" % category)
        if title in redirects_from:
            handle.write("redirect_from:\n")
            for redirect in sorted(redirects_from[title]):
                handle.write(" - %s\n" % make_url(redirect))
        handle.write("---\n\n")
        handle.write(cleanup_markdown(stdout, make_url(title)))
    os.remove(tmp_mediawiki)

print("Done")
