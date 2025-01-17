#!/usr/bin/env python3

"""Create a report of which open gerrits match which maintainers"""

import re
import sys
import json
import argparse
import fnmatch
from datetime import datetime
from io import StringIO
import dateutil.parser
import requests
from pprint import pprint


# TODO:
# 1. Cache CHANGES and MAINTAINERS files
# 2. Add reviewers from MAINTAINERS to gerrit patch
# 3. Send email nag-o-grams to authors?
# 4. Prettier reports both for maintainers and authors
# 5. How to deal with unmaintained files and components?


def get_maintainers_from_git():
    """Download the MAINTAINERS file from the fd.io repository"""
    maintainers = "https://git.fd.io/vpp/plain/MAINTAINERS"
    response = requests.get(maintainers)
    if response.status_code == 200:
        return response.text.splitlines()
    return None


def process_maintainers(text):
    """Parse MAINTAINERS file"""
    features = {}
    feature = {}
    for line in text:
        line = line.rstrip()
        if not line:
            # Save previous feature
            if feature:
                features[feature["I"]] = feature
            feature = {}
            continue
        m = re.search("^([IFMCYE]):\\s+(.*)", line)
        if m:
            tag = m.group(1)
            data = m.group(2)
            if tag in feature:
                if isinstance(feature[tag], list):
                    feature[tag].append(data)
                else:
                    feature[tag] = [feature[tag], data]
            else:
                feature[tag] = data
        else:
            if feature:
                feature["description"] = line

    features[feature["I"]] = feature
    maintainers = {}
    for _, v in features.items():
        if "M" not in v:
            print("*** missing maintainer for:", v["I"], v, file=sys.stderr)
            continue
        if isinstance(v["F"], list):
            for fl in v["F"]:
                maintainers[fl] = v["I"]
        else:
            maintainers[v["F"]] = v["I"]
    return features, maintainers


def get_component_from_filename(maintainers, filename, debug=False):
    """Return the maintainer of a given file"""
    longest_match = 0
    m = None
    for k in maintainers:
        if "*" in k:
            if fnmatch.fnmatch(filename, k):
                if len(k) > longest_match:
                    longest_match = len(k)
                    m = maintainers[k]
                    continue
        if filename.startswith(k):
            if len(k) > longest_match:
                longest_match = len(k)
                m = maintainers[k]

    return m


def get_is_verified(change):
    """Return true if patch has been verified"""
    try:
        if "approved" in change["labels"]["Verified"]:
            return True
    except KeyError:
        pass
    return False


def match_maintainer(mlist, name):
    """Return true if maintainer is in list"""
    if isinstance(mlist, list):
        for m in mlist:
            if m.startswith(name):
                return True
    else:
        if mlist.startswith(name):
            return True

    return False


def is_reviewed(feature, reviews):
    """Get reviewers"""
    for k, v in reviews.items():
        if isinstance(v, dict):
            if "display_name" in v:
                name = v["display_name"]
            else:
                name = v["name"]

            # Is person a reviewer of given feature?
            if match_maintainer(feature["M"], name):
                return True, feature["M"], k

    return False, feature["M"], None


def process_reviews(features, reviews, components):
    """Find reviews"""
    r = {}
    for c in components:
        reviewed, reviewers, result = is_reviewed(features[c], reviews)
        if reviewed:
            if result in ("approved", "liked", "recommended"):
                continue
        r[c] = {"review": result, "by": reviewers}
    return r


authorstream = {}
maintainerstream = {}
committerstream = {}


def get_stream(assigneetype, name):
    """Put different assignees on different IO streams for reporting"""
    if assigneetype == "author":
        st = authorstream
    elif assigneetype == "maintainer":
        st = maintainerstream
    elif assigneetype == "committer":
        st = committerstream
    else:
        raise ValueError()

    if name not in st:
        st[name] = StringIO()
        return st[name], True
    return st[name], False


legend = """
Legend:
-------
========================== ===========================
Status Complete            Needs To Be Addressed
========================== ===========================
V - verified               v - not verified
E - not expired            e - expired
C - no unresolved comments c - comments not resolved
R - reviewed/approved      r - review incomplete
# - days since update      # - days since update > 30
========================== ===========================

Example: [VECr 23]
    - Verified
    - Not Expired
    - Comments resolved
    - Review incomplete (Code-Review < +1)
    - 23 days since last update
"""


def print_report(report):
    """Sort by author / component or committable"""
    no_authors = 0
    no_committers = 0
    no_maintainers = 0
    for r in report:
        if r["assignee"] == "author":
            st, new = get_stream(r["assignee"], f'\n{r["owner"]}')
            st.write(
                f'\n  | `{r["number"]} <https:////gerrit.fd.io/r/c/vpp/+/{r["number"]}>`_ '
                f'[{r["status"]} {r["last_updated_days"]}]: {r["subject"]}'
            )
            no_authors += 1
        elif r["assignee"] == "maintainer":
            # Report patch on all involved components
            if r["missing_reviews_from"]:
                for c in r["missing_reviews_from"]:
                    st, new = get_stream(r["assignee"], c)
                    if new:
                        maintainers = f'**{r["missing_reviews_from"][c]["by"]}'
                        maintainers = re.sub("[\[\]']+", "", maintainers)
                        maintainers = re.sub(", ", ", **", maintainers)
                        maintainers = re.sub(" <", "** <", maintainers)
                        maintainers = re.sub(
                            " vpp-dev@lists.fd.io",
                            "** vpp-dev@lists.fd.io",
                            maintainers,
                        )
                        st.write(f"\n{c}: {maintainers}")
                    st.write(
                        f'\n  | `{r["number"]} <https:////gerrit.fd.io/r/c/vpp/+/{r["number"]}>`_ '
                        f'[{r["status"]} {r["last_updated_days"]}]: {r["subject"]}'
                    )
            else:
                st, new = get_stream(r["assignee"], "unknown maintainer")
                if new:
                    st.write("unknown maintainer:\n")
                st.write(
                    f'  | `{r["number"]} <https:////gerrit.fd.io/r/c/vpp/+/{r["number"]}>`_ '
                    f'[{r["status"]} {r["last_updated_days"]}]: {r["subject"]}'
                )
            no_maintainers += 1
        elif r["assignee"] == "committer":
            no_committers += 1
            st, new = get_stream(r["assignee"], "committer")
            st.write(
                f'\n  | `{r["number"]} <https:////gerrit.fd.io/r/c/vpp/+/{r["number"]}>`_ '
                f'[{r["status"]} {r["last_updated_days"]}]: {r["subject"]}'
            )
        else:
            print("***UNKNOWN ASSIGNEE***", file=sys.stderr)

    header = f"""
==============================================
FD.io VPP (master branch) Gerrit Change Report
==============================================
--------------------------------------------
generated on {datetime.now().strftime('%A %Y-%m-%d, %H:%M:%S')}
--------------------------------------------
"""
    print(header)
    print(legend)

    print(
        "\nCommitters:"
        "\n-----------"
        "\n| **These gerrit changes have been**\n"
        "\n    - Verified"
        "\n    - Not expired"
        "\n    - Comments resolved"
        "\n    - Approved by Maintainers"
        "\n\n| **Please perform a final review & submit.**"
    )
    for _, st in committerstream.items():
        print(st.getvalue())
    print(
        "\nMaintainers:\n------------"
        "\n| **Please review these gerrit changes.**"
        "\n\n| **NOTE: Gerrit changes may be included under more than one feature based"
        " on the modified files regardless of the feature list included on the commit headline.**"
    )
    for st in sorted(maintainerstream):
        print(maintainerstream[st].getvalue())
    print(
        "\nAuthors:"
        "\n--------"
        "\n**Please rebase and fix verification failures on these gerrit changes.**"
    )
    for st in sorted(authorstream):
        print(f"{st}:")
        print(authorstream[st].getvalue())

    print(legend)

    statistics = f"""
Statistics:
-----------
================ ===
Patches assigned
================ ===
authors          {no_authors}
maintainers      {no_maintainers}
committers       {no_committers}
================ ===
"""
    print(statistics)


def main():
    """Gerrit queue reporting tool.

    For each patch in the Gerrit VPP queue assign the patch to either the
    author, the maintainers or to the committers.

    If a patch is not verified, or it has a negative review, or it is not updated
    for the last 30 days it is assigned to an author.

    For review: If a patch is missing reviews from any of the affected
    components assign the patch to the maintainers.

    For submitting: If a patch is ready to merge assign the patch to the
    committers.

    """
    parser = argparse.ArgumentParser(description="VPP Gerrit review tool")
    parser.add_argument(
        "--maintainers-file", type=argparse.FileType("r"), required=True
    )
    parser.add_argument("--changes-file", type=argparse.FileType("r"), required=True)
    args = parser.parse_args()

    # MAINTAINERS
    features, maintainers = process_maintainers(args.maintainers_file)

    # Gerrit Queue
    # Download from gerrit or load from file
    c = json.load(args.changes_file)

    # Assign current assignee for a change:
    report = []
    for change in c:
        s = {}
        s["is_verified"] = get_is_verified(change)
        s["subject"] = change["subject"]
        s["unresolved_comment_count"] = change["unresolved_comment_count"]
        s["has_review_started"] = change["has_review_started"]
        if "display_name" in change["owner"]:
            s[
                "owner"
            ] = f'**{change["owner"]["display_name"]}** <{change["owner"]["email"]}>'
        else:
            s["owner"] = f'**{change["owner"]["name"]}** <{change["owner"]["email"]}>'
        s["number"] = change["_number"]

        try:
            reviews = change["labels"]["Code-Review"]
        except KeyError:
            reviews = {}
        last_updated = dateutil.parser.parse(change["updated"])
        s["last_updated_days"] = (datetime.now() - last_updated).days

        # Find maintainer
        rev = next(iter(change["revisions"]))
        files = change["revisions"][rev]["files"]
        components = {}

        for f in files:
            component = get_component_from_filename(maintainers, f)
            # print('COMPONENT', component, f)
            if not component:
                print(f"*** maintainer not found for: {f}", file=sys.stderr)
            else:
                if component not in components:
                    components[component] = 1
                else:
                    components[component] += 1

        # Find missing reviews
        s["missing_reviews_from"] = process_reviews(features, reviews, components)

        # Find assignee
        status = ""
        assignee = "author"
        status += "V" if s["is_verified"] else "v"
        status += "E" if s["last_updated_days"] <= 30 else "e"
        status += "C" if s["unresolved_comment_count"] == 0 else "c"

        if status.isupper():  # Author has done all required
            assignee = "maintainer"
            status += "r" if s["missing_reviews_from"] else "R"

            if not s["missing_reviews_from"]:
                assignee = "committer"

        s["assignee"] = assignee
        s["status"] = status
        report.append(s)

    print_report(report)


if __name__ == "__main__":
    sys.exit(main())
