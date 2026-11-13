"""
Web tools for Cornilia: fetch web pages, read them piece by piece, pull out their tables, build small
datasets from what the model reads, download data files, clean them, and get datasets from Hugging Face.

They are built for small local models. The code does the exact work: downloading, removing navigation and
ads, parsing tables, converting types, dropping duplicates, checking rows and saving files all happen in
plain Python. The model only decides: which link, which dataset, which cleaning steps, which fields.

Data goes to disk, not into the conversation. Every tool that produces data writes a file under the data
folder and returns a short summary: the dataset name, its path, row count, columns and a few sample rows.
Long text is cut, and the result says how to read the next part.

Install the extra packages with:  pip install -e ".[web]"
web_search also needs:              pip install -e ".[web-search]"

Settings, all read from environment variables (or set them in os.environ from your configs.py):

    CORNILIA_DATA_DIR            folder every file is written to, default ./cornilia_data. The tools never
                                 write outside it, and refuse file names with "..", slashes or a drive
    WEB_TOOLS_MAX_CHARS          the most text a tool sends back to the model, default 2000
    WEB_TOOLS_MAX_BYTES          the largest download, default 20000000 (about 20 MB)
    WEB_TOOLS_ALLOWED_DOMAINS    optional comma separated allow list such as "wikipedia.org,data.gov".
                                 When set, links to any other site are refused (subdomains are allowed)
    WEB_TOOLS_RESPECT_ROBOTS     on by default. Set to 0 to stop checking robots.txt
    WEB_TOOLS_DELAY              seconds to wait between two requests to the same site, default 1
    HF_TOKEN                     optional Hugging Face token, for gated datasets and a higher rate limit
    HF_ENDPOINT                  the Hugging Face Hub, default https://huggingface.co

Safety: only http and https links. Links to private, loopback and link-local addresses are refused, also
after a redirect. Every request has a timeout and a size limit, and downloaded content is never run.

A failed call raises an error that says what to try next, and Cornilia hands that text back to the model.
"""

import hashlib
import importlib
import io
import ipaddress
import json
import math
import os
import re
import socket
import time
import urllib.parse
import urllib.robotparser
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from cornilia.tools import tool

INSTALL_HINT = 'pip install -e ".[web]"'
USER_AGENT = "cornilia-web-tools/0.1 (+https://github.com/gamercreative/cornilia)"
TIMEOUT = 30             # seconds for one request
MAX_REDIRECTS = 5
API_MAX_BYTES = 5_000_000  # Hugging Face API answers, not data files
DATA_EXTENSIONS = (".csv", ".tsv", ".jsonl", ".json", ".parquet", ".xlsx")

FieldType = Literal["text", "integer", "number", "date", "boolean", "url"]
CleanOperation = Literal["normalize_column_names", "strip_whitespace", "drop_empty_rows", "drop_duplicate_rows",
                         "coerce_numbers", "parse_dates", "drop_constant_columns"]

Name = Annotated[str, "A short file name for the dataset, letters, digits, _ and - only, for example lebanon_weather"]


# ---------- settings ----------

def _env_int(name:str, default:int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default

def _max_chars() -> int:
    return _env_int("WEB_TOOLS_MAX_CHARS", 2000)

def _max_bytes() -> int:
    return _env_int("WEB_TOOLS_MAX_BYTES", 20_000_000)

def _delay() -> float:
    raw = os.environ.get("WEB_TOOLS_DELAY", "").strip()
    return float(raw) if raw else 1.0

def _respect_robots() -> bool:
    return os.environ.get("WEB_TOOLS_RESPECT_ROBOTS", "1").strip().lower() not in ("0", "false", "no", "off")

def _allowed_domains() -> list[str]:
    return [d.strip().lower().lstrip(".") for d in os.environ.get("WEB_TOOLS_ALLOWED_DOMAINS", "").split(",") if d.strip()]

def _data_dir() -> Path:
    folder = Path(os.environ.get("CORNILIA_DATA_DIR", "cornilia_data")).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    return folder

# an optional package, with the exact command to install it when it is missing
def _need(module:str):
    try:
        return importlib.import_module(module)
    except ImportError:
        raise RuntimeError(f"the web tools need the {module} package, which is not installed. "
                           f"Tell the user to run: {INSTALL_HINT}") from None


# ---------- files: everything stays inside the data folder ----------

# a name the model gives must be a plain file name, never a path
def _safe_name(name:str) -> str:
    name = str(name).strip()
    for extension in DATA_EXTENSIONS: # "weather.csv" means the dataset "weather"
        if name.lower().endswith(extension):
            name = name[: -len(extension)]
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name) or ".." in name):
        raise ValueError(f"{name!r} is not a plain file name. Use letters, digits, _ and - only, "
                         f"for example lebanon_weather. Folders, '..' and absolute paths are not allowed")
    return name

# the path of a file in the data folder, refusing anything that would land outside it
def _data_path(*parts:str) -> Path:
    root = _data_dir()
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"{'/'.join(parts)!r} is outside the data folder, use a plain file name")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

# turns any text (a url, a dataset id) into something _safe_name accepts
def _slug(text:str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_-")[:60]
    return slug or "data"

def _dataset_files() -> list[Path]:
    return sorted(p for p in _data_dir().iterdir() if p.is_file() and p.suffix.lower() in DATA_EXTENSIONS)

# a name not used yet: lebanon_weather, then lebanon_weather_2 and so on, so nothing is overwritten
def _unique_name(base:str) -> str:
    base = _safe_name(_slug(base))
    taken = {p.stem.lower() for p in _dataset_files()}
    name, number = base, 2
    while name.lower() in taken:
        name, number = f"{base}_{number}", number + 1
    return name

def _find_dataset(name:str) -> Path:
    name = str(name).strip()
    wanted = _safe_name(name)
    matches = [p for p in _dataset_files() if p.stem == wanted]
    if Path(name).suffix.lower() in DATA_EXTENSIONS:
        matches = [p for p in matches if p.suffix.lower() == Path(name).suffix.lower()] or matches
    if not matches:
        known = [p.stem for p in _dataset_files()][:20]
        raise FileNotFoundError(f"no dataset named {wanted!r}. Saved datasets: {known or 'none yet'}")
    return matches[0]

def _show_path(path:Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


# ---------- safe downloads ----------

# every address the host name points to, an ip address stays as it is
def _resolve_host(host:str, port:int) -> list:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ConnectionError(f"could not find the server {host}. Check the spelling of the link") from None
    return [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]

def _is_public(address) -> bool:
    if address.version == 6 and address.ipv4_mapped: # ::ffff:127.0.0.1 is 127.0.0.1
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast

def _domain_allowed(host:str) -> bool:
    allowed = _allowed_domains()
    return not allowed or any(host == d or host.endswith("." + d) for d in allowed)

# checks a link before anything is sent to it, returns its host name
def _check_url(url:str, use_allow_list:bool = True) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"only http and https links are allowed, got {url[:100]!r}")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError(f"{url[:100]!r} has no host name, a link looks like https://example.org/page")
    if parts.username or parts.password:
        raise ValueError("links with a user name or password in them are not allowed")
    if use_allow_list and not _domain_allowed(host):
        raise PermissionError(f"{host} is not in WEB_TOOLS_ALLOWED_DOMAINS. Allowed sites: {_allowed_domains()}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    for address in _resolve_host(host, port):
        if not _is_public(address):
            raise PermissionError(f"blocked {host}: it points to a private, loopback or link-local address ({address}). "
                                  f"Only public web addresses are allowed")
    return host

# the one function that talks to the network. it reads at most max_bytes into out and returns the status and headers.
# tests replace it, so no test needs the internet
def _request(url:str, headers:dict, max_bytes:int, out) -> tuple[int, dict]:
    httpx = _need("httpx")
    try:
        with httpx.Client(timeout=httpx.Timeout(TIMEOUT, connect=10), follow_redirects=False,
                          headers={"User-Agent": USER_AGENT}) as client:
            with client.stream("GET", url, headers=headers) as response:
                response_headers = {k.lower(): v for k, v in response.headers.items()}
                limit = max_bytes if response.status_code < 300 else min(max_bytes, 1_000_000)
                if int(response_headers.get("content-length") or 0) > limit:
                    raise _too_large(limit)
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise _too_large(limit)
                    out.write(chunk)
                return response.status_code, response_headers
    except httpx.TimeoutException:
        raise TimeoutError(f"the server did not answer within {TIMEOUT} seconds, try again later or use another source") from None
    except httpx.HTTPError as e:
        raise ConnectionError(f"could not download {url[:100]}: {type(e).__name__}. Check the link or try another source") from None

def _too_large(limit:int) -> Exception:
    return ValueError(f"the download is larger than WEB_TOOLS_MAX_BYTES ({limit} bytes). Look for a smaller file or a sample, "
                      f"or ask the user to raise WEB_TOOLS_MAX_BYTES")

_robots_cache: dict[str, Any] = {}
_last_request: dict[str, float] = {}

def _check_robots(url:str):
    if not _respect_robots():
        return
    parts = urllib.parse.urlsplit(url)
    site = f"{parts.scheme}://{parts.netloc}"
    if site not in _robots_cache:
        rules = None
        buffer = io.BytesIO()
        try:
            status, _ = _request(site + "/robots.txt", {}, 500_000, buffer)
            if status in (401, 403): # the usual reading: a site that hides its robots.txt allows nothing
                rules = urllib.robotparser.RobotFileParser()
                rules.disallow_all = True
            elif status == 200:
                rules = urllib.robotparser.RobotFileParser()
                rules.parse(buffer.getvalue().decode("utf-8", errors="replace").splitlines())
        except (ConnectionError, TimeoutError, ValueError):
            rules = None # no robots.txt to read means no rules
        _robots_cache[site] = rules
    rules = _robots_cache[site]
    if rules is not None and not rules.can_fetch(USER_AGENT, url):
        raise PermissionError(f"{parts.netloc} does not allow automated access to this page (robots.txt). "
                              f"Use another source, for example hf_search_datasets")

def _wait_politely(host:str):
    wait = _last_request.get(host, 0) + _delay() - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request[host] = time.monotonic()

class Fetched:
    def __init__(self, url:str, status:int, headers:dict, body:bytes | None):
        self.url = url # the final url, after redirects
        self.status = status
        self.headers = headers
        self.body = body
        self.content_type = headers.get("content-type", "").split(";")[0].strip().lower()

# downloads one link, following redirects and checking every hop. a link the model chose also goes through the
# allow list, robots.txt and the polite delay. with to_file the body is streamed to that file instead of memory
def _fetch(url:str, *, model_url:bool = True, max_bytes:int | None = None, to_file:Path | None = None,
           headers:dict | None = None) -> Fetched:
    _need("httpx") # say what to install before anything is sent
    current = str(url).strip()
    headers = dict(headers or {})
    first_host = None
    for _ in range(MAX_REDIRECTS + 1):
        host = _check_url(current, use_allow_list=model_url)
        if first_host is None:
            first_host = host
        elif host != first_host: # a token is never sent on to another site
            headers.pop("Authorization", None)
        if model_url:
            _check_robots(current)
            _wait_politely(host)

        if to_file is not None:
            with open(to_file, "wb") as out:
                status, response_headers = _request(current, headers, max_bytes or _max_bytes(), out)
            body = None
        else:
            out = io.BytesIO()
            status, response_headers = _request(current, headers, max_bytes or _max_bytes(), out)
            body = out.getvalue()

        if status in (301, 302, 303, 307, 308) and response_headers.get("location"):
            current = urllib.parse.urljoin(current, response_headers["location"])
            continue
        return Fetched(current, status, response_headers, body)
    raise ConnectionError(f"{url[:100]} redirects more than {MAX_REDIRECTS} times, try another link")

def _check_status(fetched:Fetched):
    status = fetched.status
    if status < 400:
        return
    if status == 404:
        raise FileNotFoundError(f"page not found (404) at {fetched.url[:100]}. Check the link or look for another page")
    if status in (401, 403):
        raise PermissionError(f"the site refused access ({status}). It may block bots or need a login, try another source")
    if status == 429:
        raise ConnectionError("the site is rate limiting requests (429). Wait a little, or use another source")
    raise ConnectionError(f"the server answered with error {status}, try again later or use another source")


# ---------- pages ----------

def _page_id(url:str) -> str:
    return "p_" + hashlib.sha1(url.strip().encode()).hexdigest()[:10]

def _decode_html(body:bytes, content_type_header:str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type_header or "", re.I)
    match = match or re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
    charset = match.group(1) if match else "utf-8"
    charset = charset.decode() if isinstance(charset, bytes) else charset
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")

def _html_to_text(html:str, url:str) -> tuple[str, str]:
    trafilatura = _need("trafilatura")
    text = trafilatura.extract(html, url=url, output_format="txt", include_comments=False) or ""
    title = ""
    try:
        metadata = trafilatura.extract_metadata(html)
        title = (metadata.title if metadata else "") or ""
    except Exception:
        pass
    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = re.sub(r"\s+", " ", match.group(1)).strip() if match else ""
    return text.strip(), title

def _count_tables(html:str) -> int:
    return len(re.findall(r"<table\b", html, re.I))

# finds a saved page by its id or by the link it was fetched from
def _load_page(page:str) -> dict:
    page = str(page).strip()
    page_id = _page_id(page) if page.startswith(("http://", "https://")) else page
    if not re.fullmatch(r"p_[0-9a-f]{10}", page_id):
        raise ValueError(f"{page!r} is not a page id. Use the page id fetch_page returned, such as p_1a2b3c4d5e")
    meta_path = _data_path("pages", page_id + ".json")
    if not meta_path.is_file():
        raise FileNotFoundError(f"page {page} has not been fetched yet, call fetch_page first")
    return json.loads(meta_path.read_text(encoding="utf-8"))

def _part_of(text:str, part:int) -> dict:
    size = _max_chars()
    parts = max(1, math.ceil(len(text) / size))
    if part < 1 or part > parts:
        raise ValueError(f"part must be between 1 and {parts}, this page has {parts} part(s)")
    chunk = text[(part - 1) * size: part * size]
    return {"text": chunk, "part": part, "parts": parts, "total_chars": len(text), "cut": part < parts}

def _page_result(meta:dict, part:int) -> dict:
    text = _data_path("pages", meta["page"] + ".txt").read_text(encoding="utf-8")
    result = {"page": meta["page"], "title": meta["title"], "url": meta["final_url"]}
    result.update(_part_of(text, part))
    if result["cut"]:
        result["next"] = f"read_page(page='{meta['page']}', part={part + 1})"
    return result

def _download_page(url:str) -> tuple[dict, str]:
    fetched = _fetch(url)
    _check_status(fetched)
    kind = fetched.content_type
    body = fetched.body or b""
    if kind in ("text/csv", "application/json", "application/x-ndjson", "application/vnd.apache.parquet",
                "application/octet-stream", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
        raise ValueError(f"this link is a data file ({kind}), not a web page. Use download_dataset(url=..., output_name=...)")
    if kind == "application/pdf":
        raise ValueError("this link is a PDF, which these tools can't read. Look for an HTML page or a data file instead")
    if kind and not (kind.startswith("text/") or "html" in kind or "xml" in kind):
        raise ValueError(f"this link is not a web page ({kind}). If it is a data file, use download_dataset")

    html = _decode_html(body, fetched.headers.get("content-type", ""))
    if kind == "text/plain":
        text, title = html.strip(), ""
    else:
        text, title = _html_to_text(html, fetched.url)

    page_id = _page_id(url)
    meta = {"page": page_id, "url": url, "final_url": fetched.url, "title": title,
            "chars": len(text), "tables": _count_tables(html)}
    _data_path("pages", page_id + ".html").write_text(html, encoding="utf-8")
    _data_path("pages", page_id + ".txt").write_text(text, encoding="utf-8")
    _data_path("pages", page_id + ".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta, html


# ---------- reading and profiling datasets ----------

def _flatten_columns(df):
    pd = _need("pandas")
    if isinstance(df.columns, pd.MultiIndex):
        names = []
        for column in df.columns:
            levels = [str(level) for level in column if not str(level).startswith("Unnamed")]
            names.append(" ".join(dict.fromkeys(levels)) or "column")
        df.columns = names
    df.columns = [str(c) for c in df.columns]
    return df

# the rows of a json file: a list of objects, or the first such list inside an object such as {"data": [...]}
def _json_records(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
        return [data]
    raise ValueError("the JSON file holds no rows")

def _read_dataset(path:Path):
    pd = _need("pandas")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path, encoding_errors="replace", low_memory=False)
    elif suffix == ".tsv":
        df = pd.read_csv(path, sep="\t", encoding_errors="replace", low_memory=False)
    elif suffix == ".jsonl":
        df = pd.read_json(path, lines=True, dtype=False)
        schema_path = _data_path("schemas", path.stem + ".json")
        if schema_path.is_file(): # dates saved by save_rows come back as dates
            for field, kind in json.loads(schema_path.read_text(encoding="utf-8")).items():
                if kind == "date" and field in df:
                    df[field] = pd.to_datetime(df[field], errors="coerce")
    elif suffix == ".json":
        df = pd.json_normalize(_json_records(json.loads(path.read_text(encoding="utf-8", errors="replace"))))
    elif suffix == ".parquet":
        _need("pyarrow")
        df = pd.read_parquet(path)
    elif suffix == ".xlsx":
        _need("openpyxl")
        df = pd.read_excel(path)
    else:
        raise ValueError(f"{path.name} is not a format these tools read")
    return _flatten_columns(df)

def _kind(series) -> str:
    pd = _need("pandas")
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_numeric_dtype(series):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    return "text"

def _cell(value, limit:int = 60):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "isoformat"): # dates and timestamps
        try:
            return value.isoformat()
        except ValueError: # NaT
            return None
    if hasattr(value, "item") and not hasattr(value, "__len__"): # numpy numbers
        value = value.item()
    if isinstance(value, (int, float, bool)):
        return value
    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (list, dict)) else str(value)
    return text if len(text) <= limit else text[:limit] + "..."

def _size(value) -> int:
    return len(json.dumps(value, default=str)) # the same way Cornilia sends a result to the model

# a short description of a dataset: name, path, rows, columns with type and null %, and a few sample rows.
# it shrinks (fewer samples, fewer columns) until it fits in the budget
def _profile(df, path:Path, budget:int | None = None, extra:dict | None = None) -> dict:
    budget = budget or _max_chars()
    columns = {}
    for column in df.columns:
        nulls = round(float(df[column].isna().mean()) * 100, 1) if len(df) else 0.0
        columns[str(column)] = _kind(df[column]) + (f", {nulls:g}% null" if nulls else "")

    result = {}
    for samples in (5, 3, 1, 0):
        for keep in (len(columns), 25, 12, 6):
            shown = dict(list(columns.items())[:keep])
            sample_rows = [{c: _cell(row[c]) for c in shown} for _, row in df.head(samples).iterrows()]
            result = {"name": path.stem, "path": _show_path(path), "rows": len(df), "columns": shown}
            if keep < len(columns):
                result["columns_not_shown"] = len(columns) - keep
            result.update(extra or {})
            if sample_rows:
                result["sample"] = sample_rows
            if _size(result) <= budget:
                return result
    return result


# ---------- tools: pages ----------

@tool
def fetch_page(url:Annotated[str, "The http or https link of the page"]) -> dict:
    """Downloads a web page, keeps only its main text and saves it. Returns the title, the first part of the text,
    the number of parts and how many HTML tables the page has."""
    _need("trafilatura")
    meta, _ = _download_page(url)
    if not meta["chars"] and not meta["tables"]:
        raise ValueError("no readable text or tables on this page. It may need JavaScript to show its content, try another page")
    result = _page_result(meta, 1) if meta["chars"] else {"page": meta["page"], "title": meta["title"],
                                                          "url": meta["final_url"], "text": "", "total_chars": 0}
    result["tables"] = meta["tables"]
    if meta["tables"]:
        result["tables_hint"] = f"extract_tables(page_or_url='{meta['page']}', output_name=...) saves them as CSV"
    return result

@tool
def read_page(page:Annotated[str, "The page id from fetch_page, such as p_1a2b3c4d5e"],
              part:Annotated[int, "Which part to read, 2 for the second"] = 2) -> dict:
    """Returns one part of the text of a page fetch_page already saved, to read a long page piece by piece."""
    return _page_result(_load_page(page), part)

@tool
def extract_tables(page_or_url:Annotated[str, "A page id from fetch_page, or a link"], output_name:Name) -> dict:
    """Finds every HTML table on a page and saves each one as a CSV file. Returns a short profile of each table."""
    pd = _need("pandas")
    _need("lxml")
    source = str(page_or_url).strip()
    try:
        meta = _load_page(source)
        html = _data_path("pages", meta["page"] + ".html").read_text(encoding="utf-8")
    except FileNotFoundError:
        if not source.startswith(("http://", "https://")):
            raise
        meta, html = _download_page(source)
    except ValueError:
        if not source.startswith(("http://", "https://")):
            raise ValueError(f"{source!r} is neither a page id from fetch_page nor an http or https link") from None
        meta, html = _download_page(source)

    try:
        tables = pd.read_html(io.StringIO(html), flavor="lxml")
    except ValueError:
        raise ValueError("no table found on this page. Use read_page to read the text, then create_dataset and save_rows") from None

    tables = [_flatten_columns(t) for t in tables]
    kept = [t for t in tables if len(t) >= 2 and len(t.columns) >= 2] # one cell layout tables are not data
    if not kept:
        raise ValueError("the page only has layout tables with no data. Use read_page, then create_dataset and save_rows")

    base = _safe_name(output_name)
    budget = max(250, _max_chars() // min(len(kept), 6))
    profiles, skipped_profiles = [], []
    for number, table in enumerate(kept, start=1):
        name = _unique_name(base if len(kept) == 1 else f"{base}_{number}")
        path = _data_path(name + ".csv")
        table.to_csv(path, index=False)
        if _size(profiles) + budget <= _max_chars():
            profiles.append(_profile(table, path, budget=budget))
        else:
            skipped_profiles.append(name)

    result = {"page": meta["page"], "tables_saved": len(kept), "profiles": profiles}
    if len(tables) > len(kept):
        result["layout_tables_skipped"] = len(tables) - len(kept)
    if skipped_profiles:
        result["also_saved"] = skipped_profiles[:30]
        result["hint"] = "call profile_dataset(name) to see any of the other tables"
    return result


# ---------- tools: building a dataset from what the model reads ----------

def _schema_path(name:str) -> Path:
    return _data_path("schemas", name + ".json")

FIELD_ALIASES = {"string": "text", "str": "text", "int": "integer", "float": "number", "double": "number",
                 "decimal": "number", "bool": "boolean", "datetime": "date", "link": "url"}

@tool
def create_dataset(name:Name,
                   fields:Annotated[dict, "Field name -> type. Types: text, integer, number, date, boolean, url. "
                                          "For example {\"city\": \"text\", \"rain_mm\": \"number\"}"]) -> dict:
    """Creates an empty dataset with a fixed set of fields, so save_rows can check every row. Use it for data you
    read from page text."""
    name = _safe_name(name)
    if not isinstance(fields, dict) or not fields:
        raise ValueError('fields must be an object of field name -> type, for example {"city": "text", "rain_mm": "number"}')
    if len(fields) > 30:
        raise ValueError("use at most 30 fields")
    schema = {}
    for field, kind in fields.items():
        field = str(field).strip()
        kind = FIELD_ALIASES.get(str(kind).strip().lower(), str(kind).strip().lower())
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,40}", field):
            raise ValueError(f"field name {field!r} must use letters, digits and _ only, and not start with a digit")
        if kind not in FieldType.__args__:
            raise ValueError(f"field {field!r} has type {kind!r}, use one of {list(FieldType.__args__)}")
        schema[field] = kind

    schema_path = _schema_path(name)
    if schema_path.is_file():
        existing = json.loads(schema_path.read_text(encoding="utf-8"))
        if existing != schema:
            raise ValueError(f"dataset {name} already exists with fields {existing}. Use those fields, or another name")
        return {"dataset": name, "fields": schema, "rows": _count_lines(_data_path(name + ".jsonl")), "note": "already existed"}
    if any(p.stem == name for p in _dataset_files()):
        raise ValueError(f"a dataset file named {name} already exists, pick another name")

    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    _data_path(name + ".jsonl").touch()
    return {"dataset": name, "fields": schema, "next": f"save_rows(name='{name}', rows=[...])"}

def _count_lines(path:Path) -> int:
    if not path.is_file():
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())

MONTH_FORMATS = ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%Y/%m/%d", "%Y.%m.%d")

# dates are written YYYY-MM-DD. a few unambiguous other forms are accepted, 03/04/2024 is not (day or month first?)
def _parse_date(value):
    if isinstance(value, (date, datetime)) or value is None:
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for pattern in MONTH_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    raise ValueError(f"write dates as YYYY-MM-DD, got {text[:30]!r}")

# light, exact clean up before pydantic checks a value: trim text, empty means missing, "1,200" means 1200
def _pre_clean(kind:str):
    def clean(value):
        if isinstance(value, str):
            value = value.strip()
            if value == "" or value.lower() in ("null", "none", "n/a", "na"):
                return None
        if kind == "text" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if kind in ("integer", "number") and isinstance(value, str):
            value = re.sub(r"(?<=\d)[,_ ](?=\d{3}\b)", "", value).lstrip("$€£")
            if kind == "integer" and re.fullmatch(r"-?\d+\.0+", value):
                value = value.split(".")[0]
        if kind == "date":
            return _parse_date(value)
        return value
    return clean

def _row_model(schema:dict):
    pydantic = _need("pydantic")
    from pydantic.functional_validators import BeforeValidator
    python_types = {"text": str, "integer": int, "number": Annotated[float, pydantic.Field(allow_inf_nan=False)],
                    "date": date, "boolean": bool, "url": pydantic.AnyHttpUrl}
    fields = {}
    for field, kind in schema.items():
        fields[field] = (Annotated[python_types[kind] | None, BeforeValidator(_pre_clean(kind))], None)
    config = pydantic.ConfigDict(extra="forbid", str_strip_whitespace=True)
    return pydantic.create_model("Row", __config__=config, **fields)

def _reason(error) -> str:
    parts = []
    for item in error.errors()[:3]:
        where = ".".join(str(x) for x in item.get("loc", ())) or "row"
        message = item.get("msg", "invalid").replace("Value error, ", "")
        if item.get("type") == "extra_forbidden":
            message = "is not a field of this dataset"
        parts.append(f"{where}: {message}")
    return "; ".join(parts)

@tool
def save_rows(name:Annotated[str, "The dataset name from create_dataset"],
              rows:Annotated[list[dict], "Rows to add, each an object with the dataset's fields. Leave out a value you don't know"]) -> dict:
    """Checks rows against the dataset's fields and saves the valid ones. Duplicates are skipped. Returns how many
    were saved and why any row was rejected, so you can fix and resend only those."""
    name = _safe_name(name)
    schema_path = _schema_path(name)
    if not schema_path.is_file():
        raise FileNotFoundError(f"dataset {name} has no fields yet, call create_dataset(name='{name}', fields=...) first")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("rows must be a list of objects")
    pydantic = _need("pydantic")
    Row = _row_model(schema)

    data_path = _data_path(name + ".jsonl")
    seen = set()
    if data_path.is_file():
        with open(data_path, encoding="utf-8") as f:
            seen = {line.strip() for line in f if line.strip()}

    saved, duplicates, rejected, new_lines = 0, 0, [], []
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            rejected.append({"row": number, "reason": "not an object of field -> value"})
            continue
        try:
            values = Row.model_validate(row).model_dump(mode="json")
        except pydantic.ValidationError as e:
            rejected.append({"row": number, "reason": _reason(e)})
            continue
        if all(value is None for value in values.values()):
            rejected.append({"row": number, "reason": "the row has no values"})
            continue
        line = json.dumps(values, ensure_ascii=False, sort_keys=True)
        if line in seen:
            duplicates += 1
            continue
        seen.add(line)
        new_lines.append(line)
        saved += 1

    if new_lines:
        with open(data_path, "a", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")

    result = {"dataset": name, "saved": saved, "duplicates_skipped": duplicates, "rejected": len(rejected),
              "total_rows": len(seen), "path": _show_path(data_path)}
    if rejected:
        result["rejected_rows"] = rejected[:10]
        result["hint"] = "fix the rejected rows and send only those again. Leave out values you don't know, don't guess them"
    while _size(result) > _max_chars() and result.get("rejected_rows"):
        result["rejected_rows"] = result["rejected_rows"][:-1]
    return result


# ---------- tools: data files ----------

def _detect_format(path:Path, content_type:str, url:str) -> str:
    with open(path, "rb") as f:
        head = f.read(4096)
    if head.startswith(b"PAR1"):
        return ".parquet"
    if head.startswith(b"\x1f\x8b"):
        raise ValueError("this file is compressed (gzip), which download_dataset doesn't open. Look for an uncompressed link")
    if head.startswith(b"PK"):
        return ".xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValueError("this is an old .xls Excel file, which download_dataset doesn't open. Look for a CSV or .xlsx link")

    suffix = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    if suffix == ".ndjson":
        return ".jsonl"
    if suffix in (".csv", ".tsv", ".json", ".jsonl"):
        return suffix
    by_type = {"text/csv": ".csv", "text/tab-separated-values": ".tsv", "application/json": ".json",
               "application/x-ndjson": ".jsonl", "application/jsonl": ".jsonl"}
    if content_type in by_type:
        return by_type[content_type]

    text = head.decode("utf-8", errors="replace").lstrip("﻿ \r\n\t")
    if text.startswith("<"):
        raise ValueError("this link is a web page, not a data file. Use fetch_page or extract_tables")
    if text.startswith("["):
        return ".json"
    if text.startswith("{"):
        lines = [line for line in text.splitlines() if line.strip()]
        return ".jsonl" if len(lines) > 1 and lines[1].lstrip().startswith("{") else ".json"
    first_line = text.splitlines()[0] if text else ""
    return ".tsv" if first_line.count("\t") > first_line.count(",") else ".csv"

@tool
def download_dataset(url:Annotated[str, "Direct link to a CSV, JSON, JSONL, Parquet or XLSX file"], output_name:Name) -> dict:
    """Downloads a data file, checks its format, saves it and returns a short profile."""
    _need("pandas")
    name = _unique_name(output_name)
    partial = _data_path(name + ".download")
    try:
        fetched = _fetch(url, to_file=partial)
        _check_status(fetched)
        extension = _detect_format(partial, fetched.content_type, fetched.url)
        path = _data_path(name + extension)
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    try:
        df = _read_dataset(path)
    except Exception as e:
        path.unlink(missing_ok=True)
        raise ValueError(f"the file could not be read as {extension[1:].upper()} ({type(e).__name__}: {str(e)[:150]}). "
                         f"Check that the link is the data file itself") from None
    return _profile(df, path, extra={"source": fetched.url[:200]})

@tool
def profile_dataset(name:Annotated[str, "The name of a saved dataset"]) -> dict:
    """Describes a saved dataset: rows, columns with types and missing values, and a few sample rows."""
    path = _find_dataset(name)
    return _profile(_read_dataset(path), path)

def _normalize_name(name:str) -> str:
    name = re.sub(r"[^0-9a-z]+", "_", str(name).strip().lower()).strip("_")
    return name or "column"

def _to_number(series):
    pd = _need("pandas")
    text = series.astype("string").str.strip()
    text = text.str.replace(r"(?<=\d)[,\s](?=\d{3}\b)", "", regex=True).str.replace(r"^[$€£]|%$", "", regex=True)
    return pd.to_numeric(text, errors="coerce")

def _mostly(converted, original, share:float = 0.9) -> bool:
    present = original.notna() & (original.astype("string").str.strip() != "")
    return present.sum() > 0 and converted[present].notna().mean() >= share

def _null_cells(df) -> int:
    return int(df.isna().sum().sum())

def _apply(df, operation:str) -> tuple[Any, str]:
    pd = _need("pandas")
    text_columns = [c for c in df.columns if df[c].dtype == object or pd.api.types.is_string_dtype(df[c])]

    if operation == "normalize_column_names":
        names, used = [], {}
        for column in df.columns:
            name = _normalize_name(column)
            used[name] = used.get(name, 0) + 1
            names.append(name if used[name] == 1 else f"{name}_{used[name]}")
        df.columns = names
        return df, "column names are lower case with _"
    if operation == "strip_whitespace":
        for column in text_columns:
            df[column] = df[column].map(lambda v: (v.strip() or None) if isinstance(v, str) else v)
        return df, f"trimmed {len(text_columns)} text column(s), empty text is now missing"
    if operation == "drop_empty_rows":
        blank = df.isna() | df.apply(lambda col: col.map(lambda v: isinstance(v, str) and not v.strip()))
        before = len(df)
        df = df[~blank.all(axis=1)]
        return df, f"dropped {before - len(df)} empty row(s)"
    if operation == "drop_duplicate_rows":
        before = len(df)
        df = df.drop_duplicates()
        return df, f"dropped {before - len(df)} duplicate row(s)"
    if operation == "coerce_numbers":
        changed = []
        for column in text_columns:
            converted = _to_number(df[column])
            if _mostly(converted, df[column]):
                df[column] = converted
                changed.append(str(column))
        return df, f"numbers in: {changed or 'none'} (a column is converted when 90% of its values are numbers)"
    if operation == "parse_dates":
        changed = []
        for column in text_columns:
            sample = df[column].dropna().astype(str)
            if sample.empty or not sample.str.contains(r"\d").all():
                continue
            converted = pd.to_datetime(df[column], errors="coerce", format="mixed")
            if _mostly(converted, df[column]):
                df[column] = converted
                changed.append(str(column))
        return df, f"dates in: {changed or 'none'} (a column is converted when 90% of its values are dates)"
    if operation == "drop_constant_columns":
        constant = [c for c in df.columns if df[c].astype("string").nunique(dropna=False) <= 1]
        return df.drop(columns=constant), f"dropped constant column(s): {constant or 'none'}"
    raise ValueError(f"unknown operation {operation!r}, use one of {list(CleanOperation.__args__)}")

@tool
def clean_dataset(name:Annotated[str, "The name of a saved dataset"],
                  operations:Annotated[list[CleanOperation], "Cleaning steps, applied in this order"]) -> dict:
    """Cleans a saved dataset with fixed steps and saves the result as a new dataset. The original is kept.
    Returns rows, columns and missing values before and after."""
    pd = _need("pandas")
    path = _find_dataset(name)
    if not operations:
        raise ValueError(f"pick at least one operation from {list(CleanOperation.__args__)}")
    unknown = [op for op in operations if op not in CleanOperation.__args__]
    if unknown:
        raise ValueError(f"unknown operation(s) {unknown}, use only {list(CleanOperation.__args__)}")

    df = _read_dataset(path)
    before = {"rows": len(df), "columns": len(df.columns), "missing_cells": _null_cells(df)}
    steps = []
    for operation in operations:
        df, what = _apply(df, operation)
        steps.append(f"{operation}: {what}")
    df = df.reset_index(drop=True)

    output = _unique_name(path.stem + "_clean")
    out_path = _data_path(output + (".parquet" if path.suffix.lower() == ".parquet" else ".csv"))
    if out_path.suffix == ".parquet":
        df.to_parquet(out_path, index=False)
    else:
        df.to_csv(out_path, index=False)

    after = {"rows": len(df), "columns": len(df.columns), "missing_cells": _null_cells(df)}
    result = {"input": path.stem, "output": output, "path": _show_path(out_path), "before": before, "after": after, "steps": steps}
    room = _max_chars() - _size(result)
    if room > 200:
        result["profile"] = _profile(df, out_path, budget=room - 20)
        result["profile"].pop("path", None)
    return result


# ---------- tools: Hugging Face ----------

def _hf_headers() -> dict:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}

def _hub() -> str:
    return os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")

DATASETS_SERVER = "https://datasets-server.huggingface.co"

def _hf_json(url:str, what_next:str):
    fetched = _fetch(url, model_url=False, max_bytes=API_MAX_BYTES, headers=_hf_headers())
    try:
        data = json.loads(fetched.body or b"null")
    except ValueError:
        data = None
    if fetched.status >= 400:
        message = data.get("error", "") if isinstance(data, dict) else ""
        hint = " (it may be private or gated: set HF_TOKEN and accept its terms on the website)" if fetched.status in (401, 403, 404) else ""
        raise ConnectionError(f"Hugging Face answered {fetched.status}: {str(message)[:200]}{hint}. {what_next}")
    return data

def _hf_tags(tags:list) -> dict:
    found = {}
    for prefix, key in (("size_categories:", "size"), ("task_categories:", "tasks"), ("language:", "languages"),
                        ("modality:", "modality"), ("format:", "formats")):
        values = [t[len(prefix):] for t in tags if isinstance(t, str) and t.startswith(prefix)]
        if values:
            found[key] = values[0] if key == "size" else values[:3]
    return found

@tool
def hf_search_datasets(query:Annotated[str, "A few general words, for example weather lebanon"],
                       limit:Annotated[int, "How many results, 1 to 10"] = 5) -> dict:
    """Searches Hugging Face for datasets. Returns each dataset's id, a short description, downloads, last update,
    size and tasks."""
    limit = max(1, min(int(limit), 10))
    params = urllib.parse.urlencode({"search": query.strip(), "limit": limit, "sort": "downloads", "direction": -1})
    items = _hf_json(f"{_hub()}/api/datasets?{params}", "Try other search words")
    if not items:
        raise LookupError(f"no Hugging Face dataset matches {query!r}. Try fewer or more general words, "
                          f"for example one word such as 'weather'")
    results = []
    for item in items:
        description = re.sub(r"\s+", " ", str(item.get("description") or "")).strip()
        entry = {"id": item.get("id"), "description": description[:150], "downloads": item.get("downloads"),
                 "last_modified": str(item.get("lastModified") or "")[:10]}
        if item.get("gated"):
            entry["gated"] = True
        entry.update(_hf_tags(item.get("tags") or []))
        results.append(entry)
    result = {"query": query, "results": results, "next": "hf_get_dataset(dataset_id=...) saves rows from one of them"}
    while _size(result) > _max_chars() and len(result["results"]) > 1:
        result["results"].pop()
    return result

@tool
def hf_get_dataset(dataset_id:Annotated[str, "The dataset id, such as owner/name"],
                   config:Annotated[str | None, "The config (subset), leave out for the first one"] = None,
                   split:Annotated[str, "The split, such as train or test"] = "train",
                   max_rows:Annotated[int, "How many rows to save, at most 100000"] = 1000) -> dict:
    """Saves rows of a Hugging Face dataset to a file and returns a short profile. Tells you the available configs
    and splits if the ones given don't exist."""
    pd = _need("pandas")
    dataset_id = str(dataset_id).strip().strip("/")
    if not re.fullmatch(r"[\w.-]+(/[\w.-]+)?", dataset_id):
        raise ValueError(f"{dataset_id!r} is not a dataset id, it looks like owner/name. Use hf_search_datasets to find one")
    max_rows = max(1, min(int(max_rows), 100_000))
    quoted = urllib.parse.quote(dataset_id, safe="/")

    data = _hf_json(f"{DATASETS_SERVER}/splits?dataset={quoted}", "Check the id with hf_search_datasets")
    splits = data.get("splits", []) if isinstance(data, dict) else []
    if not splits:
        raise LookupError(f"{dataset_id} has no splits the dataset viewer can read. Try another dataset")
    configs = list(dict.fromkeys(s["config"] for s in splits))
    notes = []
    if config is None:
        config = "default" if "default" in configs else configs[0]
        if len(configs) > 1:
            notes.append(f"used config {config!r}, others: {configs[:10]}")
    elif config not in configs:
        raise ValueError(f"{dataset_id} has no config {config!r}. Configs: {configs[:20]}")
    config_splits = [s["split"] for s in splits if s["config"] == config]
    if split not in config_splits:
        if split == "train" and config_splits: # the default, so pick what exists instead of failing
            notes.append(f"no train split, used {config_splits[0]!r}")
            split = config_splits[0]
        else:
            raise ValueError(f"config {config!r} has no split {split!r}. Splits: {config_splits}")

    query = urllib.parse.urlencode({"dataset": dataset_id, "config": config, "split": split})
    name = _unique_name(f"hf_{dataset_id.replace('/', '__')}_{config}_{split}")

    if max_rows <= 2000: # a sample, read page by page through the rows api
        rows, total, cut_cells = [], None, False
        while len(rows) < max_rows:
            length = min(100, max_rows - len(rows))
            page = _hf_json(f"{DATASETS_SERVER}/rows?{query}&offset={len(rows)}&length={length}", "Try a smaller max_rows")
            total = page.get("num_rows_total", total)
            batch = page.get("rows", [])
            rows += [item["row"] for item in batch]
            cut_cells = cut_cells or any(item.get("truncated_cells") for item in batch)
            if len(batch) < length:
                break
        path = _data_path(name + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        if cut_cells:
            notes.append("some long cells were shortened by the Hugging Face rows api")
        df = pd.DataFrame(rows)
    else: # more rows, download the parquet export file by file until there are enough
        _need("pyarrow")
        listing = _hf_json(f"{DATASETS_SERVER}/parquet?{query}", "Try a smaller max_rows")
        files = [f for f in listing.get("parquet_files", []) if f.get("config") == config and f.get("split") == split]
        if not files:
            raise LookupError("this split has no parquet export. Try max_rows of 2000 or less")
        total = sum(f.get("num_rows", 0) for f in files) or None
        frames, budget = [], _max_bytes()
        for number, item in enumerate(files):
            if item.get("size") and item["size"] > budget:
                notes.append("stopped at WEB_TOOLS_MAX_BYTES, raise it for more rows")
                break
            part = _data_path(f"{name}.part{number}.download")
            try:
                fetched = _fetch(item["url"], model_url=False, max_bytes=budget, to_file=part, headers=_hf_headers())
                _check_status(fetched)
                budget -= part.stat().st_size
                frames.append(pd.read_parquet(part))
            finally:
                part.unlink(missing_ok=True)
            if sum(len(f) for f in frames) >= max_rows:
                break
        if not frames:
            raise ValueError("the first parquet file is larger than WEB_TOOLS_MAX_BYTES. Use max_rows of 2000 or less for a sample")
        df = pd.concat(frames, ignore_index=True).head(max_rows)
        path = _data_path(name + ".parquet")
        df.to_parquet(path, index=False)

    extra = {"dataset_id": dataset_id, "config": config, "split": split}
    if total:
        extra["rows_in_split"] = total
    if notes:
        extra["notes"] = notes
    return _profile(_flatten_columns(df), path, extra=extra)


# ---------- tools: search ----------

@tool
def web_search(query:Annotated[str, "What to search for"],
               limit:Annotated[int, "How many results, 1 to 10"] = 5) -> dict:
    """Searches the web and returns titles, links and short snippets. Then use fetch_page on a link."""
    try:
        from ddgs import DDGS
    except ImportError:
        raise RuntimeError('web_search needs the ddgs package. Tell the user to run: pip install -e ".[web-search]". '
                           'Until then ask the user for a link, or use hf_search_datasets') from None
    limit = max(1, min(int(limit), 10))
    try:
        found = DDGS(timeout=10).text(query, max_results=limit) or []
    except Exception as e:
        raise ConnectionError(f"the search failed or was blocked ({type(e).__name__}). Try again with other words, "
                              f"or ask the user for a link") from None
    results, outside = [], 0
    for item in found:
        link = str(item.get("href") or "")
        host = (urllib.parse.urlsplit(link).hostname or "").lower()
        if not link.startswith(("http://", "https://")):
            continue
        if not _domain_allowed(host):
            outside += 1
            continue
        results.append({"title": _cell(item.get("title"), 100), "url": link, "snippet": _cell(item.get("body"), 200)})
    if not results:
        raise LookupError(f"no results for {query!r}" + (f" inside WEB_TOOLS_ALLOWED_DOMAINS ({outside} outside)" if outside else "")
                          + ". Try other words")
    result = {"query": query, "results": results}
    if outside:
        result["skipped_outside_allowed_domains"] = outside
    while _size(result) > _max_chars() and len(result["results"]) > 1:
        result["results"].pop()
    return result
