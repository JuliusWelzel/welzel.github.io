#!/usr/bin/env python3
"""Collect publications and regenerate the publication list of the website.

Sources (each can be switched off in publications/config.yml):

* ORCID public API      - works listed on the ORCID record
* OpenAlex              - works linked to the ORCID iD
* Google Scholar        - the public profile page (best effort, Scholar
                          often blocks automated requests)
* Crossref              - metadata (authors, journal, volume, pages) for
                          every DOI, and DOI lookup for Scholar-only items

Entries are merged by DOI and by title, preprints are folded into their
published version, and the manual curation in publications/config.yml
(exclude / overrides / add) is applied last.

Outputs:

* publications/publications.yml  - merged data (machine readable)
* publications/_publications.md  - markdown included by publications/index.qmd
* publications/_recent.md        - the newest entries, included by the home page
* projects/_pubs/<project>.md    - publications of each project (see "projects"
                                   in publications/config.yml)

When a source fails, entries previously found through it are kept, so a
temporary outage never empties the page. If the list would shrink by more
than a quarter, nothing is written and the script exits with status 2.

Usage:
    python scripts/update_publications.py            # fetch and write
    python scripts/update_publications.py --offline  # re-render from publications.yml
    python scripts/update_publications.py --dry-run  # fetch, report, write nothing
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import html
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
PUB_DIR = ROOT / "publications"
CONFIG_FILE = PUB_DIR / "config.yml"
DATA_FILE = PUB_DIR / "publications.yml"
MARKDOWN_FILE = PUB_DIR / "_publications.md"
RECENT_FILE = PUB_DIR / "_recent.md"
RECENT_COUNT = 5
PROJECT_PUB_DIR = ROOT / "projects" / "_pubs"

TIMEOUT = 30
MAX_SHRINK = 0.25

# Types shown on the website, in display order for badges.
TYPE_LABELS = {
    "article": None,
    "conference": "Conference",
    "chapter": "Book chapter",
    "preprint": "Preprint",
    "dataset": "Dataset",
    "software": "Software",
    "thesis": "Thesis",
    "other": None,
}

# Types that are never listed (corrections, peer reviews, ...).
DROP_TYPES = {
    "erratum", "paratext", "peer-review", "retraction", "supplementary-materials",
    "component", "libguides", "grant", "review-report", "editorial-note",
}

PREPRINT_DOI_PREFIXES = (
    "10.1101/",     # bioRxiv, medRxiv
    "10.64898/",    # medRxiv (2026 onwards)
    "10.31234/",    # PsyArXiv
    "10.31219/",    # OSF Preprints
    "10.31235/",    # SocArXiv
    "10.31222/",    # MetaArXiv
    "10.48550/",    # arXiv
    "10.21203/",    # Research Square
    "10.2139/",     # SSRN
    "10.20944/",    # Preprints.org
    "10.36227/",    # TechRxiv
    "10.22541/",    # Authorea
    "10.32942/",    # EcoEvoRxiv
    "10.51224/",    # SportRxiv
    "10.7287/",     # PeerJ Preprints
)

PREPRINT_VENUES = re.compile(
    r"\b(biorxiv|medrxiv|psyarxiv|arxiv|osf preprints|research square|ssrn|"
    r"preprints\.org|techrxiv|authorea|sportrxiv|metaarxiv|socarxiv)\b",
    re.IGNORECASE,
)

NAME_PARTICLES = {
    "van", "von", "der", "den", "de", "del", "della", "di", "da", "du", "dos",
    "das", "la", "le", "ter", "ten", "zu", "op", "het", "'t", "bin", "al",
}


class SourceError(RuntimeError):
    """A source could not be queried; its previous entries are kept."""


# --------------------------------------------------------------------------
# helpers


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def clean_text(value: Any) -> str:
    """Strip markup (JATS/HTML/MathML) and normalise whitespace."""
    if value is None:
        return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", "", text)
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", text).strip()


def norm_doi(doi: Any) -> str:
    if not doi:
        return ""
    doi = str(doi).strip()
    doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    return doi.strip().rstrip(".").lower()


def norm_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", clean_text(title)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def strip_version(title: str) -> str:
    """'toolbox: v1.2.0' -> 'toolbox' (collapses software releases)."""
    return re.sub(r"[\s:,(-]*\bv?\d+(\.\d+){1,3}\S*\)?$", "", title).strip()


def titles_match(a: str, b: str) -> bool:
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if min(len(na), len(nb)) < 30:
        return False
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.93


def split_name(display: str) -> tuple[str, str]:
    """'Jaap H. van Dieën' -> ('van Dieën', 'Jaap H.')."""
    display = clean_text(display)
    if "," in display:
        family, given = display.split(",", 1)
        return family.strip(), given.strip()
    parts = display.split()
    if len(parts) <= 1:
        return display, ""
    i = len(parts) - 1
    while i > 1 and parts[i - 1].lower() in NAME_PARTICLES:
        i -= 1
    return " ".join(parts[i:]), " ".join(parts[:i])


def initials(given: str) -> str:
    """'Julius' -> 'J.', 'Anna-Lena M.' -> 'A.-L. M.', 'JS' -> 'J. S.'."""
    given = clean_text(given)
    if not given:
        return ""
    out = []
    for token in re.sub(r"\.(?!-)", ". ", given).split():
        if "-" in token.strip("-"):
            out.append("-".join(p[0].upper() + "." for p in token.split("-") if p))
        elif token.isupper() and len(token.rstrip(".")) <= 3 and len(token) > 1 and "." not in token:
            out.extend(ch + "." for ch in token)  # Scholar style "JS"
        else:
            out.append(token[0].upper() + ".")
    return " ".join(out)


def format_author(family: str, given: str) -> str:
    family = clean_text(family)
    ini = initials(given)
    return f"{family}, {ini}" if ini else family


def year_of(*values: Any) -> int | None:
    for value in values:
        if value is None:
            continue
        match = re.search(r"(19|20)\d{2}", str(value))
        if match:
            return int(match.group(0))
    return None


def is_preprint_doi(doi: str) -> bool:
    return doi.startswith(PREPRINT_DOI_PREFIXES)


def normalise_type(raw: str, doi: str = "", venue: str = "") -> str:
    raw = (raw or "").lower().replace("_", "-")  # ORCID: JOURNAL_ARTICLE
    if raw in DROP_TYPES:
        return "drop"
    if is_preprint_doi(doi) or raw in {"preprint", "posted-content", "working-paper"} or (
        venue and PREPRINT_VENUES.search(venue)
    ):
        return "preprint"
    if raw in {"journal-article", "article", "review", "letter", "editorial",
               "journal-issue", "magazine-article", "newspaper-article"}:
        return "article"
    if raw in {"proceedings-article", "conference-paper", "conference-abstract",
               "conference-poster", "conference-output", "proceedings"}:
        return "conference"
    if raw in {"book-chapter", "book", "book-section", "monograph", "edited-book",
               "reference-entry", "book-part"}:
        return "chapter"
    if raw in {"dataset", "data-set"}:
        return "dataset"
    if raw in {"software"}:
        return "software"
    if raw in {"dissertation", "dissertation-thesis", "supervised-student-publication"}:
        return "thesis"
    return "other"


class Http:
    def __init__(self, contact: str):
        self.session = requests.Session()
        agent = "welzel.github.io publication updater (https://github.com/JuliusWelzel/welzel.github.io"
        agent += f"; mailto:{contact})" if contact else ")"
        self.session.headers["User-Agent"] = agent

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None,
            retries: int = 3) -> requests.Response:
        last: Exception | None = None
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=TIMEOUT)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                return resp
            except requests.RequestException as exc:
                last = exc
                time.sleep(2 ** attempt * 2)
        raise SourceError(f"{url}: {last}")


def record(**fields: Any) -> dict:
    rec = {
        "doi": "", "title": "", "authors": [], "year": None, "date": "",
        "venue": "", "volume": "", "issue": "", "pages": "", "article_number": "", "type": "other",
        "url": "", "preprint_url": "", "sources": [],
    }
    rec.update({k: v for k, v in fields.items() if v not in (None, "")})
    return rec


# --------------------------------------------------------------------------
# sources


def fetch_orcid(http: Http, orcid: str) -> list[dict]:
    resp = http.get(f"https://pub.orcid.org/v3.0/{orcid}/works",
                    headers={"Accept": "application/json"})
    if resp.status_code != 200:
        raise SourceError(f"ORCID returned HTTP {resp.status_code}")
    out = []
    for group in resp.json().get("group", []):
        summaries = group.get("work-summary") or []
        if not summaries:
            continue
        summary = summaries[0]
        ids = (summary.get("external-ids") or {}).get("external-id") or []
        ids += (group.get("external-ids") or {}).get("external-id") or []
        doi = ""
        for ext in ids:
            if (ext.get("external-id-type") or "").lower() == "doi":
                doi = norm_doi(ext.get("external-id-value") or
                               (ext.get("external-id-normalized") or {}).get("value"))
                if doi:
                    break
        title = clean_text((((summary.get("title") or {}).get("title")) or {}).get("value"))
        date = summary.get("publication-date") or {}
        year = year_of(((date.get("year") or {}).get("value")))
        month = ((date.get("month") or {}).get("value")) or ""
        venue = clean_text((summary.get("journal-title") or {}).get("value"))
        url = ((summary.get("url") or {}).get("value")) or ""
        rtype = normalise_type(summary.get("type", ""), doi, venue)
        if not title or rtype == "drop":
            continue
        out.append(record(doi=doi, title=title, year=year,
                          date=f"{year}-{month}" if year and month else (str(year) if year else ""),
                          venue=venue, type=rtype, url=url, sources=["orcid"]))
    return out


def openalex_authors(work: dict) -> list[str]:
    names = []
    for authorship in work.get("authorships") or []:
        display = (authorship.get("author") or {}).get("display_name") or authorship.get("raw_author_name")
        if display:
            names.append(format_author(*split_name(display)))
    return names


def fetch_openalex(http: Http, orcid: str, contact: str) -> list[dict]:
    params = {"filter": f"author.orcid:{orcid}", "per-page": "200", "cursor": "*"}
    if contact:
        params["mailto"] = contact
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    out = []
    while params.get("cursor"):
        resp = http.get("https://api.openalex.org/works", params=params)
        if resp.status_code != 200:
            raise SourceError(f"OpenAlex returned HTTP {resp.status_code}")
        payload = resp.json()
        for work in payload.get("results", []):
            doi = norm_doi(work.get("doi"))
            location = work.get("primary_location") or {}
            venue = clean_text((location.get("source") or {}).get("display_name"))
            rtype = normalise_type(work.get("type_crossref") or work.get("type") or "", doi, venue)
            if work.get("type") in DROP_TYPES or work.get("is_retracted"):
                rtype = "drop"
            title = clean_text(work.get("title") or work.get("display_name"))
            if not title or rtype == "drop":
                continue
            biblio = work.get("biblio") or {}
            first, last = biblio.get("first_page"), biblio.get("last_page")
            pages = f"{first}–{last}" if first and last and first != last else (first or "")
            out.append(record(
                doi=doi, title=title, authors=openalex_authors(work),
                year=work.get("publication_year"), date=work.get("publication_date") or "",
                venue=venue, volume=biblio.get("volume") or "", issue=biblio.get("issue") or "",
                pages=pages, type=rtype,
                url=location.get("landing_page_url") or "", sources=["openalex"],
            ))
        params["cursor"] = (payload.get("meta") or {}).get("next_cursor")
        if not payload.get("results"):
            break
    return out


class ScholarParser:
    """Minimal parser for rows of a Google Scholar profile page."""

    ROW = re.compile(r'<tr class="gsc_a_tr">(.*?)</tr>', re.S)
    TITLE = re.compile(r'<a[^>]*href="([^"]*)"[^>]*class="gsc_a_at"[^>]*>(.*?)</a>'
                       r'|<a[^>]*class="gsc_a_at"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.S)
    GRAY = re.compile(r'<div class="gs_gray">(.*?)</div>', re.S)
    YEAR = re.compile(r'<span class="gsc_a_h[^"]*">(\d{4})</span>')

    @classmethod
    def rows(cls, page: str) -> list[dict]:
        out = []
        for row in cls.ROW.findall(page):
            title_match = cls.TITLE.search(row)
            if not title_match:
                continue
            href = html.unescape(title_match.group(1) or title_match.group(3) or "")
            title = clean_text(title_match.group(2) or title_match.group(4))
            grays = [clean_text(g) for g in cls.GRAY.findall(row)]
            year_match = cls.YEAR.search(row)
            authors = []
            if grays:
                for name in grays[0].split(","):
                    name = name.strip()
                    if not name:
                        continue
                    if name in {"...", "…"}:
                        authors.append("…")  # Scholar truncated the author list
                        continue
                    family, given = split_name(name)
                    authors.append(format_author(family, given))
            venue = re.sub(r",?\s*(19|20)\d{2}$", "", grays[1]).strip() if len(grays) > 1 else ""
            volume = issue = pages = ""
            # "Gait & Posture 57, 1-2" / "Scientific Reports 14 (1), 4301"
            parts = re.match(r"^(.*?[A-Za-z)])\s+(\d+)(?:\s*\(([^)]*)\))?(?:,\s*(\S+))?$", venue)
            if parts:
                venue, volume, issue, pages = parts.group(1), parts.group(2), parts.group(3) or "", parts.group(4) or ""
                pages = pages.replace("-", "–")
            out.append(record(
                title=title, authors=authors, year=int(year_match.group(1)) if year_match else None,
                venue=venue, volume=volume, issue=issue, pages=pages, type=normalise_type("", "", venue) if venue else "other",
                url=f"https://scholar.google.com{href}" if href.startswith("/") else href,
                sources=["google_scholar"],
            ))
        return out


def fetch_scholar(http: Http, user: str) -> list[dict]:
    out: list[dict] = []
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    for start in range(0, 2000, 100):
        resp = http.get("https://scholar.google.com/citations",
                        params={"user": user, "hl": "en", "cstart": str(start),
                                "pagesize": "100", "sortby": "pubdate"},
                        headers=headers, retries=2)
        text = resp.text
        if resp.status_code != 200 or "gsc_a_tr" not in text and "gsc_a_b" not in text:
            raise SourceError(f"Google Scholar blocked the request (HTTP {resp.status_code})")
        rows = ScholarParser.rows(text)
        out.extend(rows)
        if len(rows) < 100:
            break
        time.sleep(3)
    return out


def crossref_work(http: Http, doi: str) -> dict | None:
    resp = http.get(f"https://api.crossref.org/works/{quote(doi, safe='/')}")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise SourceError(f"Crossref returned HTTP {resp.status_code} for {doi}")
    return resp.json().get("message")


def crossref_fields(msg: dict) -> dict:
    authors = []
    for author in msg.get("author") or []:
        if author.get("family"):
            authors.append(format_author(author["family"], author.get("given", "")))
        elif author.get("name"):
            authors.append(clean_text(author["name"]))
    date = None
    for key in ("published-print", "published-online", "published", "issued", "posted", "created"):
        parts = ((msg.get(key) or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            date = parts
            break
    venue = clean_text(msg.get("container-title"))
    if not venue and msg.get("type") == "posted-content":
        venue = clean_text(msg.get("institution", [{}])[0].get("name") if msg.get("institution") else "")
        venue = venue or clean_text(msg.get("publisher"))
    doi = norm_doi(msg.get("DOI"))
    fields = {
        "title": clean_text(msg.get("title")),
        "authors": authors,
        "venue": venue,
        "volume": clean_text(msg.get("volume")),
        "issue": clean_text(msg.get("issue")),
        "pages": clean_text(msg.get("page")).replace("-", "–"),
        "article_number": clean_text(msg.get("article-number")),
        "type": normalise_type(msg.get("type", ""), doi, venue),
    }
    if date:
        fields["year"] = int(date[0])
        fields["date"] = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(date) if p)
    return {k: v for k, v in fields.items() if v}


def crossref_find_doi(http: Http, title: str, family: str) -> str:
    resp = http.get("https://api.crossref.org/works",
                    params={"query.bibliographic": title, "query.author": family, "rows": "5",
                            "select": "DOI,title,author"})
    if resp.status_code != 200:
        return ""
    for item in (resp.json().get("message") or {}).get("items", []):
        families = {(a.get("family") or "").lower() for a in item.get("author") or []}
        if family.lower() in families and titles_match(clean_text(item.get("title")), title):
            return norm_doi(item.get("DOI"))
    return ""


# --------------------------------------------------------------------------
# merging

def merge_into(target: dict, other: dict) -> dict:
    """Fill empty fields of target from other and union the sources."""
    for key, value in other.items():
        if key == "sources":
            target["sources"] = sorted(set(target.get("sources", [])) | set(value))
        elif key == "authors":
            # Prefer complete author lists over truncated (Scholar) ones; the
            # truncation marker itself does not count as an author.
            def named(names: list[str]) -> int:
                return len([n for n in names or [] if n not in {"…", "..."}])
            if named(value) > named(target.get("authors")):
                target["authors"] = value
            elif (named(value) == named(target.get("authors")) and target.get("authors")
                  and "…" in target["authors"] and "…" not in (value or [])):
                target["authors"] = value
        elif not target.get(key) and value:
            target[key] = value
    return target


def rank(rec: dict) -> tuple:
    """Higher is better when choosing which duplicate to keep."""
    order = ["article", "conference", "chapter", "thesis", "other", "dataset", "software", "preprint"]
    rtype = rec.get("type", "other")
    return (
        rtype != "preprint",
        bool(rec.get("doi")),
        -order.index(rtype) if rtype in order else -len(order),
        bool(rec.get("venue")),
        len(rec.get("authors") or []),
        rec.get("date") or "",
    )


SUPPLEMENT_TITLE = re.compile(
    r"^(additional file|supplementary (material|information|data|file)|supplemental (material|data)"
    r"|table s?\d+|figure s?\d+|data sheet \d+)\b", re.IGNORECASE)


def is_supplement(rec: dict) -> bool:
    """Supplementary files deposited separately (e.g. figshare 'Additional file 1 of ...')."""
    return bool(SUPPLEMENT_TITLE.match(rec.get("title", "")))


def merge_by_doi(records: Iterable[dict]) -> list[dict]:
    by_doi: dict[str, dict] = {}
    rest: list[dict] = []
    for rec in records:
        if rec.get("doi"):
            if rec["doi"] in by_doi:
                merge_into(by_doi[rec["doi"]], rec)
            else:
                by_doi[rec["doi"]] = dict(rec)
        else:
            rest.append(dict(rec))
    return list(by_doi.values()) + rest


def merge_by_title(records: list[dict]) -> list[dict]:
    """Fold duplicates with (nearly) the same title.

    A preprint matching a published entry becomes its 'preprint_url'.
    Software and data releases that differ only by version number collapse
    into one.
    """
    kept: list[dict] = []
    for rec in sorted(records, key=rank, reverse=True):
        title = rec["title"]
        if rec.get("type") in {"software", "dataset"}:
            title = strip_version(title)
        match = None
        for other in kept:
            other_title = (strip_version(other["title"]) if other.get("type") in {"software", "dataset"}
                           else other["title"])
            if not titles_match(title, other_title):
                na, nb = norm_title(title), norm_title(other_title)
                prefix = (len(min(na, nb, key=len)) >= 40
                          and (na.startswith(nb) or nb.startswith(na))
                          and not (rec.get("doi") and other.get("doi")))
                if not prefix:
                    continue
            # Same title but clearly different DOIs of the same type and
            # several years apart (e.g. a replication) stay separate.
            if (rec.get("doi") and other.get("doi") and rec["doi"] != other["doi"]
                    and rec["type"] == other["type"]
                    and rec["type"] not in {"software", "dataset", "preprint"}):
                # Different DOIs: companion papers ("Protocol of..." vs
                # "Results of...", "Part 1" vs "Part 2") are near-identical in
                # text, so only an identical title counts as the same work.
                if norm_title(rec["title"]) != norm_title(other["title"]):
                    continue
                if (rec.get("year") and other.get("year") and abs(rec["year"] - other["year"]) > 1):
                    continue
            match = other
            break
        if match is None:
            kept.append(rec)
            continue
        if rec.get("type") == "preprint" and match.get("type") != "preprint":
            link = f"https://doi.org/{rec['doi']}" if rec.get("doi") else rec.get("url", "")
            match["preprint_url"] = match.get("preprint_url") or link
            match["sources"] = sorted(set(match["sources"]) | set(rec["sources"]))
        else:
            doi = match.get("doi")
            merge_into(match, rec)
            match["doi"] = doi or rec.get("doi", "")
    return kept


def apply_curation(records: list[dict], config: dict) -> list[dict]:
    excluded_dois = {norm_doi(e.get("doi")) for e in config.get("exclude") or [] if e.get("doi")}
    excluded_titles = {norm_title(e.get("title")) for e in config.get("exclude") or [] if e.get("title")}
    overrides = {norm_doi(k): v for k, v in (config.get("overrides") or {}).items()}
    out = []
    for rec in records:
        if rec.get("doi") in excluded_dois or norm_title(rec["title"]) in excluded_titles:
            continue
        if rec.get("doi") in overrides:
            rec.update({k: v for k, v in overrides[rec["doi"]].items() if k != "doi"})
        out.append(rec)
    return out


def manual_records(config: dict) -> list[dict]:
    out = []
    for entry in config.get("add") or []:
        entry = dict(entry)
        entry["doi"] = norm_doi(entry.get("doi"))
        entry["type"] = entry.get("type") or "article"
        entry["authors"] = [clean_text(a) for a in entry.get("authors") or []]
        entry["year"] = year_of(entry.get("year"))
        entry["date"] = str(entry.get("date") or entry.get("year") or "")
        out.append(record(**{**entry, "sources": ["manual"]}))
    return out


# --------------------------------------------------------------------------
# output

MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#+|@$^~])")


def md_escape(text: str) -> str:
    return MD_SPECIAL.sub(r"\\\1", text)


def is_me(author: str, config: dict) -> bool:
    family = config["author"]["family"].lower()
    initial = config["author"].get("given_initial", "").lower()
    name = author.lower()
    if not name.startswith(family + ","):
        return name == config["author"]["name"].lower()
    return name[len(family) + 1:].strip().startswith(initial)


def format_authors(authors: list[str], config: dict) -> str:
    """APA 7 author list; the site owner's name in bold.

    Up to 20 authors are listed. Longer lists show the first 19, an
    ellipsis and the final author; if the owner falls into the omitted
    part, their name is kept visible between two ellipses.
    """
    if not authors:
        return ""
    marked = [f"**{md_escape(a)}**" if is_me(a, config) else md_escape(a) for a in authors]
    if any(a in {"…", "..."} for a in authors):  # already abbreviated by hand
        return ", ".join(marked)
    if len(marked) > 20:
        me = next((i for i, a in enumerate(authors) if is_me(a, config)), None)
        shown = ", ".join(marked[:19])
        if me is not None and 19 <= me < len(marked) - 1:
            shown += ", … " + marked[me]
        return shown + ", … " + marked[-1]
    if len(marked) == 1:
        return marked[0]
    return ", ".join(marked[:-1]) + ", & " + marked[-1]


def _keep_word(word: str, keep: set[str]) -> bool:
    core = word.strip("([{\"'“‘”’)]}.,;:!?")
    base = re.sub(r"['’]s$", "", core)
    return (
        not core
        or any(ch.isdigit() for ch in core)
        or (len(core) == 1 and core.isupper())    # Vitamin D, Phase I
        or any(ch.isupper() for ch in core[1:])   # EEG, KielMAT, MoBI
        or not core[0].isupper()                  # already lower case / iPad
        or core.lower() in keep or base.lower() in keep
    )


def sentence_case(title: str, keep: set[str]) -> str:
    """Convert Title Case to APA sentence case.

    Titles that are not in title case are returned unchanged. The first word,
    words after a colon, acronyms, words with digits and the proper nouns in
    `keep` keep their capitalisation.
    """
    if re.search(r"[äöüßÄÖÜ]|\b(und|bei|der|die|das|von|mit|für|zur|zum|eine?)\b", title):
        return title  # German titles already capitalise nouns
    words = re.findall(r"[A-Za-z][A-Za-z'’]*", title)
    long_words = [w for w in words[1:] if len(w) > 3]
    if not long_words or sum(w[0].isupper() for w in long_words) / len(long_words) < 0.6:
        return title
    out, start = [], True
    for token in re.split(r"(\s+)", title):
        if not token or token.isspace():
            out.append(token)
            continue
        parts = token.split("-")
        converted = []
        for i, part in enumerate(parts):
            if (start and i == 0) or _keep_word(part, keep):
                converted.append(part)
            else:
                converted.append(part.lower())
        out.append("-".join(converted))
        start = token.endswith((":", "?", "!"))
    return "".join(out)


def end_sentence(text: str) -> str:
    return text if text.endswith(("?", "!", ".")) else text + "."


def entry_link(rec: dict) -> str:
    if rec.get("doi"):
        return f"https://doi.org/{rec['doi']}"
    return rec.get("url", "")


def format_entry(rec: dict, config: dict) -> str:
    """One APA 7 reference as a markdown list item."""
    keep = {w.lower() for w in config.get("keep_case") or []}
    rtype = rec.get("type", "other")
    authors = format_authors(rec.get("authors") or [], config)
    year = rec.get("year") or "n.d."
    title = rec["title"].strip()
    if config.get("sentence_case", True):
        title = sentence_case(title, keep)
    title = md_escape(title.rstrip("."))
    venue = md_escape(rec.get("venue") or "")

    parts = [f"{authors} ({year})." if authors else f"({year})."]
    if rtype in {"preprint", "dataset", "software", "thesis"}:
        described = {"dataset": " \\[Data set\\]", "software": " \\[Computer software\\]",
                     "thesis": " \\[Thesis\\]"}.get(rtype, "")
        parts.append(f"*{title}*{described}." if described
                     else f"*{title}*" + ("" if title.endswith(("?", "!", ".")) else "."))
        if venue:
            parts.append(end_sentence(venue))
    elif rtype == "chapter":
        parts.append(end_sentence(title))
        if venue:
            pages = f" (pp. {md_escape(str(rec['pages']))})" if rec.get("pages") else ""
            parts.append(f"In *{venue}*{pages}.")
    else:
        parts.append(end_sentence(title))
        if venue:
            source = f"*{venue}*"
            if rec.get("volume"):
                source += f", *{md_escape(str(rec['volume']))}*"
                if rec.get("issue"):
                    source += f"({md_escape(str(rec['issue']))})"
            if rec.get("article_number"):
                source += f", Article {md_escape(str(rec['article_number']))}"
            elif rec.get("pages"):
                source += f", {md_escape(str(rec['pages']))}"
            parts.append(source + ".")
    link = entry_link(rec)
    if link:
        parts.append(f"[{link}]({link}){{.pub-doi}}")

    badges = []
    label = TYPE_LABELS.get(rtype)
    if label and rtype not in {"dataset", "software"}:
        badges.append(f"[{label}]{{.pub-badge .pub-{rtype}}}")
    if rec.get("preprint_url"):
        badges.append(f"[Preprint]({rec['preprint_url']}){{.pub-badge .pub-preprint-link}}")
    if badges:
        parts.append(" ".join(badges))
    return "- " + " ".join(parts)


def sorted_records(records: list[dict]) -> list[dict]:
    """Newest year first; within a year newest date first, then by title."""
    def key(rec: dict) -> tuple:
        parts = [int(x) for x in re.findall(r"\d+", str(rec.get("date") or ""))[1:3]] + [0, 0]
        return (-(rec.get("year") or 0), -parts[0], -parts[1], norm_title(rec["title"]))
    return sorted(records, key=key)


def render_markdown(records: list[dict], config: dict, updated: str) -> str:
    author = config["author"]
    lines = [
        "<!-- Generated by scripts/update_publications.py. Do not edit by hand;",
        "     use publications/config.yml to add, hide or correct entries. -->",
        "",
        "::: {.pub-meta}",
        f"{len(records)} publications · updated automatically every week from "
        f"[ORCID](https://orcid.org/{author['orcid']}), "
        f"[Google Scholar](https://scholar.google.com/citations?user={author['google_scholar']}), "
        "[OpenAlex](https://openalex.org) and [Crossref](https://www.crossref.org) · "
        f"last change {updated}",
        ":::",
        "",
    ]
    by_year: dict[Any, list[dict]] = {}
    for rec in sorted_records(records):
        by_year.setdefault(rec.get("year") or "Undated", []).append(rec)
    for year, items in by_year.items():
        lines += [f"## {year} {{#year-{year}}}", "", "::: {.pub-list}"]
        lines += [format_entry(rec, config) for rec in items]
        lines += [":::", ""]
    return "\n".join(lines)


def render_recent(records: list[dict], config: dict) -> str:
    recent = [r for r in sorted_records(records) if r.get("type") not in {"software", "dataset"}]
    lines = [
        "<!-- Generated by scripts/update_publications.py. Do not edit by hand. -->",
        "",
        "::: {.pub-list .pub-recent}",
    ]
    lines += [format_entry(rec, config) for rec in recent[:RECENT_COUNT]]
    lines += [":::", ""]
    return "\n".join(lines)


def render_projects(records: list[dict], config: dict) -> dict[str, str]:
    """Publication lists for the project pages, keyed by project slug."""
    out = {}
    for slug, refs in (config.get("projects") or {}).items():
        items = []
        for ref in refs or []:
            doi = norm_doi(ref)
            match = next((r for r in records if (doi and r.get("doi") == doi)
                          or titles_match(r["title"], str(ref))), None)
            if match is None:
                log(f"WARNING project '{slug}': no publication matches {ref}")
            elif match not in items:
                items.append(match)
        lines = ["<!-- Generated by scripts/update_publications.py from the \"projects\" section",
                 "     of publications/config.yml. Do not edit by hand. -->", "", "::: {.pub-list}"]
        lines += [format_entry(rec, config) for rec in sorted_records(items)]
        lines += [":::", ""]
        out[slug] = "\n".join(lines)
    return out


def dump_records(records: list[dict]) -> list[dict]:
    keys = ["doi", "title", "authors", "year", "date", "venue", "volume", "issue", "pages",
            "article_number", "type", "url", "preprint_url", "sources"]
    out = []
    for rec in sorted_records(records):
        out.append({k: rec[k] for k in keys if rec.get(k) not in (None, "", [])})
    return out


# --------------------------------------------------------------------------
# main


def load_previous() -> dict:
    if DATA_FILE.exists():
        return yaml.safe_load(DATA_FILE.read_text(encoding="utf-8")) or {}
    return {}


def collect(config: dict, previous: list[dict]) -> tuple[list[dict], set[str]]:
    http = Http(config.get("contact_email", ""))
    author = config["author"]
    enabled = config.get("sources") or {}
    found: list[dict] = []
    failed: set[str] = set()

    fetchers = {
        "orcid": lambda: fetch_orcid(http, author["orcid"]),
        "openalex": lambda: fetch_openalex(http, author["orcid"], config.get("contact_email", "")),
        "google_scholar": lambda: fetch_scholar(http, author["google_scholar"]),
    }
    for name, fetch in fetchers.items():
        if not enabled.get(name, True):
            continue
        try:
            items = fetch()
            log(f"{name}: {len(items)} entries")
            found.extend(items)
        except (SourceError, requests.RequestException, ValueError) as exc:
            failed.add(name)
            log(f"WARNING {name} failed, keeping its previous entries: {exc}")

    previous_by_doi = {p["doi"]: p for p in previous if p.get("doi")}

    if enabled.get("crossref", True):
        # Scholar-only entries: try to find their DOI.
        known_titles = [r["title"] for r in found if r.get("doi")]
        for rec in found:
            if rec.get("doi") or "google_scholar" not in rec["sources"]:
                continue
            if any(titles_match(rec["title"], t) for t in known_titles):
                continue
            resolved = next((p["doi"] for p in previous
                             if p.get("doi") and titles_match(rec["title"], p["title"])), "")
            if resolved:
                rec["doi"] = resolved
                continue
            try:
                rec["doi"] = crossref_find_doi(http, rec["title"], author["family"])
            except SourceError as exc:
                log(f"WARNING Crossref search failed for '{rec['title'][:60]}': {exc}")
        # Authoritative metadata for every DOI.
        for doi in sorted({r["doi"] for r in found if r.get("doi")}):
            try:
                msg = crossref_work(http, doi)
            except SourceError as exc:
                log(f"WARNING Crossref lookup failed for {doi}: {exc}")
                if doi in previous_by_doi:
                    found.append({**previous_by_doi[doi], "sources": []})
                continue
            if msg:
                fields = crossref_fields(msg)
                found.append(record(doi=doi, **fields, sources=[]))
            time.sleep(0.1)

    # Crossref metadata must win over the discovery sources, so merge it first.
    crossref_first = sorted(found, key=lambda r: 0 if not r["sources"] else 1)
    merged = merge_by_doi(crossref_first)

    # Keep what a source that failed now contributed last time: previous
    # entries it found are kept, and fields it supplied are filled back in.
    # Entries from the former hand-maintained list ("legacy") are kept until
    # a run in which every source succeeds.
    if failed:
        retain = failed | {"legacy"}
        for old in previous:
            if not set(old.get("sources", [])) & retain or "manual" in old.get("sources", []):
                continue
            match = next((r for r in merged if old.get("doi") and r.get("doi") == old["doi"]), None)
            if match is None:
                match = next((r for r in merged if titles_match(old["title"], r["title"])
                              and not (old.get("doi") and r.get("doi"))), None)
            if match is None:
                merged.append(record(**old))
            else:
                merge_into(match, record(**old))
    return merged, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="re-render from publications.yml without fetching")
    parser.add_argument("--dry-run", action="store_true", help="fetch and report, but write nothing")
    parser.add_argument("--force", action="store_true", help="write even if the list shrinks a lot")
    args = parser.parse_args()

    config = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    previous_data = load_previous()
    previous = [p for p in previous_data.get("publications") or [] if "manual" not in p.get("sources", [])]

    if args.offline:
        records, failed = [record(**p) for p in previous], set()
    else:
        records, failed = collect(config, previous)

    records = [r for r in records if r.get("title") and r.get("type") != "drop" and not is_supplement(r)]
    records = merge_by_title(merge_by_doi(records + manual_records(config)))
    records = apply_curation(records, config)
    data = dump_records(records)

    if not args.offline and failed == {"orcid", "openalex", "google_scholar"} & set(
            k for k, v in (config.get("sources") or {}).items() if v):
        log("ERROR all sources failed; nothing written")
        return 1

    old_list = previous_data.get("publications") or []
    if old_list and len(data) < len(old_list) * (1 - MAX_SHRINK) and not args.force:
        log(f"ERROR the list would shrink from {len(old_list)} to {len(data)} entries; "
            "nothing written (use --force to override)")
        return 2

    changed = data != old_list
    updated = dt.date.today().isoformat() if changed else str(previous_data.get("updated") or dt.date.today())
    log(f"{len(data)} publications ({'changed' if changed else 'unchanged'})")
    for rec in data:
        if not any(o.get("doi") == rec.get("doi") and o.get("title") == rec.get("title") for o in old_list):
            log(f"  + {rec.get('year')} {rec['title'][:90]}")
    for old in old_list:
        if not any(o.get("doi") == old.get("doi") and o.get("title") == old.get("title") for o in data):
            log(f"  - {old.get('year')} {old['title'][:90]}")

    if args.dry_run:
        return 0

    if changed or not DATA_FILE.exists():
        header = ("# Generated by scripts/update_publications.py. Do not edit by hand;\n"
                  "# use publications/config.yml to add, hide or correct entries.\n")
        body = yaml.safe_dump({"updated": updated, "count": len(data), "publications": data},
                              sort_keys=False, allow_unicode=True, width=1000)
        DATA_FILE.write_text(header + body, encoding="utf-8")
    outputs = [(MARKDOWN_FILE, render_markdown(records, config, updated)),
               (RECENT_FILE, render_recent(records, config))]
    outputs += [(PROJECT_PUB_DIR / f"{slug}.md", text)
                for slug, text in render_projects(records, config).items()]
    for path, text in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
    # Remove lists of projects that are no longer configured.
    current = {path for path, _ in outputs}
    for stale in PROJECT_PUB_DIR.glob("*.md"):
        if stale not in current:
            stale.unlink()

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")
            fh.write(f"count={len(data)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
