# tests for examples/web_tools. they run offline: the one function that talks to the network is replaced
# by a fake that serves local fixture files. skipped when the [web] extra is not installed
import importlib.util
import ipaddress
import json
import sys
from pathlib import Path

import pytest

for package in ("httpx", "trafilatura", "pandas", "pyarrow", "pydantic", "lxml"):
    pytest.importorskip(package)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "web"


def load_web_module():
    spec = importlib.util.spec_from_file_location("web_tools_under_test", ROOT / "examples" / "web_tools" / "web.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def web(tmp_path, monkeypatch):
    module = load_web_module()
    monkeypatch.setenv("CORNILIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WEB_TOOLS_DELAY", "0")
    monkeypatch.delenv("WEB_TOOLS_ALLOWED_DOMAINS", raising=False)
    monkeypatch.delenv("WEB_TOOLS_MAX_CHARS", raising=False)

    # every host name resolves to one public address, ip addresses stay as they are
    real_resolve = module._resolve_host
    def fake_resolve(host, port):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return [ipaddress.ip_address("93.184.216.34")]
        return real_resolve(host, port)
    monkeypatch.setattr(module, "_resolve_host", fake_resolve)

    module.served = {} # url -> (status, headers, body)
    module.requested = []
    def fake_request(url, headers, max_bytes, out):
        module.requested.append(url)
        status, response_headers, body = module.served.get(url, (404, {}, b"not found"))
        if len(body) > max_bytes:
            raise module._too_large(max_bytes)
        out.write(body)
        return status, response_headers
    monkeypatch.setattr(module, "_request", fake_request)
    return module


def serve(web, url, fixture=None, body=None, content_type="text/html; charset=utf-8", status=200):
    if fixture:
        body = (FIXTURES / fixture).read_bytes()
    web.served[url] = (status, {"content-type": content_type}, body)


def test_fetch_page_keeps_the_main_text_and_drops_navigation(web):
    serve(web, "https://weather.example/rain", "article.html")
    result = web.fetch_page("https://weather.example/rain")
    assert result["title"] == "Rainfall in Lebanon"
    assert "825 millimetres" in result["text"]
    assert "SUNGLASSES" not in result["text"] and "Cookie settings" not in result["text"]
    assert result["cut"] is False and result["tables"] == 0
    saved = Path(web._data_dir(), "pages", result["page"] + ".txt").read_text(encoding="utf-8")
    assert saved.endswith(result["text"])


def test_long_page_is_cut_and_read_part_by_part(web, monkeypatch):
    monkeypatch.setenv("WEB_TOOLS_MAX_CHARS", "200")
    serve(web, "https://weather.example/rain", "article.html")
    first = web.fetch_page("https://weather.example/rain")
    assert first["cut"] is True and len(first["text"]) == 200 and first["parts"] > 1
    assert first["next"] == f"read_page(page='{first['page']}', part=2)"

    parts = [first["text"]]
    for part in range(2, first["parts"] + 1):
        parts.append(web.read_page(first["page"], part)["text"])
    full = Path(web._data_dir(), "pages", first["page"] + ".txt").read_text(encoding="utf-8")
    assert "".join(parts) == full
    assert web.read_page("https://weather.example/rain", 2)["text"] == parts[1] # the link works as well as the id
    with pytest.raises(ValueError, match="part must be between 1 and"):
        web.read_page(first["page"], first["parts"] + 1)


def test_extract_tables_saves_csv_and_skips_layout_tables(web):
    serve(web, "https://weather.example/cities", "tables.html")
    page = web.fetch_page("https://weather.example/cities")
    assert page["tables"] == 3

    result = web.extract_tables(page["page"], "city_rain")
    assert result["tables_saved"] == 2 and result["layout_tables_skipped"] == 1
    first = result["profiles"][0]
    assert first["name"] == "city_rain_1" and first["rows"] == 4
    assert first["columns"]["Rainfall (mm)"] == "integer"
    assert first["columns"]["Elevation (m)"] == "integer" # "1,000" became 1000
    assert (web._data_dir() / "city_rain_1.csv").is_file() and (web._data_dir() / "city_rain_2.csv").is_file()
    assert len(json.dumps(result)) <= 2000


def test_extract_tables_without_tables_points_to_read_page(web):
    serve(web, "https://weather.example/rain", "article.html")
    with pytest.raises(ValueError, match="read_page"):
        web.extract_tables("https://weather.example/rain", "nothing")


def test_save_rows_validates_coerces_and_reports_rejected_rows(web):
    web.create_dataset("rain", {"city": "text", "rain_mm": "number", "measured": "date", "source": "url"})
    result = web.save_rows("rain", [
        {"city": "  Beirut ", "rain_mm": "1,200", "measured": "2024-01-05", "source": "https://weather.example/rain"},
        {"city": "Beirut", "rain_mm": 1200, "measured": "2024-01-05", "source": "https://weather.example/rain"}, # duplicate
        {"city": "Zahle", "rain_mm": "a lot"},
        {"city": "Tripoli", "measured": "03/04/2024"},
        {"city": "Baalbek", "temperature": 30},
        {"city": "Sidon"}, # missing values are fine, they are saved as null
        {},
    ])
    assert result["saved"] == 2 and result["duplicates_skipped"] == 1 and result["rejected"] == 4
    reasons = {item["row"]: item["reason"] for item in result["rejected_rows"]}
    assert "rain_mm" in reasons[3]
    assert "YYYY-MM-DD" in reasons[4]
    assert "temperature" in reasons[5] and "not a field" in reasons[5]
    assert "no values" in reasons[7]

    lines = (web._data_dir() / "rain.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0]) == {"city": "Beirut", "rain_mm": 1200.0, "measured": "2024-01-05",
                                    "source": "https://weather.example/rain"}
    assert len(lines) == 2

    again = web.save_rows("rain", [{"city": "Sidon"}]) # already saved, so nothing new is written
    assert again["saved"] == 0 and again["duplicates_skipped"] == 1 and again["total_rows"] == 2
    assert web.profile_dataset("rain")["rows"] == 2


def test_create_dataset_rejects_unknown_types_and_save_rows_needs_a_schema(web):
    with pytest.raises(ValueError, match="use one of"):
        web.create_dataset("bad", {"city": "place"})
    with pytest.raises(FileNotFoundError, match="create_dataset"):
        web.save_rows("unknown", [{"city": "Beirut"}])


@pytest.mark.parametrize("name", ["../escape", "/etc/passwd", "a/b", "..", "C:\\temp\\x", "", "x" * 200])
def test_names_that_leave_the_data_folder_are_refused(web, name):
    with pytest.raises(ValueError, match="plain file name"):
        web.create_dataset(name, {"city": "text"})
    assert not (web._data_dir().parent / "escape.jsonl").exists()


@pytest.mark.parametrize("url", ["http://127.0.0.1/admin", "http://10.0.0.5/", "http://169.254.169.254/latest/meta-data/",
                                 "http://[::1]/", "http://192.168.1.1/", "http://[::ffff:127.0.0.1]/"])
def test_private_addresses_are_blocked(web, url):
    with pytest.raises(PermissionError, match="private, loopback or link-local"):
        web.fetch_page(url)
    assert web.requested == [] # nothing was sent


def test_a_redirect_to_a_private_address_is_blocked(web):
    web.served["https://weather.example/go"] = (302, {"location": "http://127.0.0.1:8080/secret"}, b"")
    with pytest.raises(PermissionError, match="private"):
        web.fetch_page("https://weather.example/go")
    assert web.requested == ["https://weather.example/robots.txt", "https://weather.example/go"]


def test_only_http_and_allowed_domains(web, monkeypatch):
    with pytest.raises(ValueError, match="only http and https"):
        web.fetch_page("file:///etc/passwd")
    monkeypatch.setenv("WEB_TOOLS_ALLOWED_DOMAINS", "wikipedia.org")
    with pytest.raises(PermissionError, match="WEB_TOOLS_ALLOWED_DOMAINS"):
        web.fetch_page("https://weather.example/rain")


def test_robots_txt_is_respected(web):
    serve(web, "https://weather.example/robots.txt", body=b"User-agent: *\nDisallow: /private\n", content_type="text/plain")
    serve(web, "https://weather.example/private/page", "article.html")
    with pytest.raises(PermissionError, match="robots.txt"):
        web.fetch_page("https://weather.example/private/page")


def test_download_dataset_and_clean_it_into_a_new_file(web):
    serve(web, "https://data.example/rain.csv", "messy.csv", content_type="text/csv")
    profile = web.download_dataset("https://data.example/rain.csv", "rain")
    assert profile["name"] == "rain" and profile["rows"] == 5

    result = web.clean_dataset("rain", ["normalize_column_names", "strip_whitespace", "drop_empty_rows",
                                        "drop_duplicate_rows", "coerce_numbers", "parse_dates", "drop_constant_columns"])
    assert result["input"] == "rain" and result["output"] == "rain_clean"
    assert result["before"]["rows"] == 5 and result["after"]["rows"] == 3
    assert result["before"]["columns"] == 4 and result["after"]["columns"] == 3 # source is always "station"
    columns = result["profile"]["columns"]
    assert list(columns) == ["city_name", "rain_mm", "measured_on"]
    assert columns["rain_mm"] == "integer" and columns["measured_on"] == "date"
    assert result["profile"]["sample"][0]["city_name"] == "Beirut"

    original = (web._data_dir() / "rain.csv").read_bytes()
    assert original == (FIXTURES / "messy.csv").read_bytes() # the original is never changed
    assert web.clean_dataset("rain", ["drop_duplicate_rows"])["output"] == "rain_clean_2" # nor any earlier output


def test_download_dataset_refuses_a_web_page_and_a_file_too_large(web, monkeypatch):
    serve(web, "https://data.example/page", "article.html")
    with pytest.raises(ValueError, match="fetch_page"):
        web.download_dataset("https://data.example/page", "page")
    monkeypatch.setenv("WEB_TOOLS_MAX_BYTES", "10")
    serve(web, "https://data.example/rain.csv", "messy.csv", content_type="text/csv")
    with pytest.raises(ValueError, match="WEB_TOOLS_MAX_BYTES"):
        web.download_dataset("https://data.example/rain.csv", "rain")
    assert list(web._data_dir().glob("*.download")) == []


def test_hf_get_dataset_lists_configs_and_saves_a_sample(web):
    server = web.DATASETS_SERVER
    splits = {"splits": [{"dataset": "me/rain", "config": "default", "split": "train"},
                         {"dataset": "me/rain", "config": "default", "split": "test"}]}
    serve(web, f"{server}/splits?dataset=me/rain", body=json.dumps(splits).encode(), content_type="application/json")
    with pytest.raises(ValueError, match=r"Configs: \['default'\]"):
        web.hf_get_dataset("me/rain", config="hourly")
    with pytest.raises(ValueError, match=r"Splits: \['train', 'test'\]"):
        web.hf_get_dataset("me/rain", split="validation")

    rows = {"num_rows_total": 3, "rows": [{"row_idx": i, "row": {"city": c, "rain": r}, "truncated_cells": []}
                                          for i, (c, r) in enumerate([("Beirut", 825), ("Zahle", 600), ("Tyre", 700)])]}
    serve(web, f"{server}/rows?dataset=me%2Frain&config=default&split=train&offset=0&length=100",
          body=json.dumps(rows).encode(), content_type="application/json")
    profile = web.hf_get_dataset("me/rain", max_rows=100)
    assert profile["rows"] == 3 and profile["rows_in_split"] == 3
    assert profile["columns"] == {"city": "text", "rain": "integer"}
    assert (web._data_dir() / (profile["name"] + ".jsonl")).is_file()


def test_hf_search_datasets_returns_short_results(web):
    items = [{"id": "me/rain", "description": "Daily  rain\nin Lebanon " * 30, "downloads": 12,
              "lastModified": "2026-01-02T03:04:05.000Z", "tags": ["size_categories:1K<n<10K", "task_categories:time-series-forecasting"]}]
    url = f"{web._hub()}/api/datasets?search=rain&limit=5&sort=downloads&direction=-1"
    serve(web, url, body=json.dumps(items).encode(), content_type="application/json")
    result = web.hf_search_datasets("rain")
    first = result["results"][0]
    assert first["id"] == "me/rain" and first["size"] == "1K<n<10K" and first["last_modified"] == "2026-01-02"
    assert len(first["description"]) <= 150 and "\n" not in first["description"]


def test_every_tool_loads_with_a_short_schema():
    from cornilia.tools import LoadTools
    tools = LoadTools(ROOT / "examples" / "web_tools")
    assert len(tools) <= 11
    for item in tools.values():
        schema = item.get_schema()["function"]
        assert len(schema["description"]) < 250, schema["name"]


@pytest.mark.parametrize("file_name, content_type", [("rain.parquet", "application/octet-stream"),
                                                     ("rain.xlsx", "application/octet-stream"),
                                                     ("rain.jsonl", "application/octet-stream"),
                                                     ("rain", "application/json")])
def test_download_dataset_detects_the_format(web, tmp_path, file_name, content_type):
    pytest.importorskip("openpyxl")
    import pandas as pd
    df = pd.DataFrame({"city": ["Beirut", "Zahle"], "rain": [825, 600]})
    source = tmp_path / "source"
    if file_name.endswith(".parquet"):
        df.to_parquet(source)
    elif file_name.endswith(".xlsx"):
        df.to_excel(source, index=False)
    elif file_name.endswith(".jsonl"):
        df.to_json(source, orient="records", lines=True)
    else: # a json api answer with the rows inside an object
        source.write_text(json.dumps({"count": 2, "data": df.to_dict(orient="records")}))
    serve(web, f"https://data.example/{file_name}", body=source.read_bytes(), content_type=content_type)
    profile = web.download_dataset(f"https://data.example/{file_name}", "rain")
    assert profile["rows"] == 2 and profile["columns"] == {"city": "text", "rain": "integer"}


def test_hf_get_dataset_uses_the_parquet_export_for_many_rows(web, tmp_path, monkeypatch):
    import pandas as pd
    monkeypatch.setenv("HF_TOKEN", "secret")
    server = web.DATASETS_SERVER
    splits = {"splits": [{"dataset": "me/rain", "config": "default", "split": "train"}]}
    serve(web, f"{server}/splits?dataset=me/rain", body=json.dumps(splits).encode(), content_type="application/json")
    file_url = "https://huggingface.co/api/datasets/me/rain/parquet/default/train/0.parquet"
    listing = {"parquet_files": [{"config": "default", "split": "train", "url": file_url, "size": 100}]}
    serve(web, f"{server}/parquet?dataset=me%2Frain&config=default&split=train", body=json.dumps(listing).encode(),
          content_type="application/json")
    pd.DataFrame({"hour": range(5000), "rain": [0.5] * 5000}).to_parquet(tmp_path / "rain.parquet")
    web.served[file_url] = (302, {"location": "https://cdn.example/rain.parquet"}, b"")
    serve(web, "https://cdn.example/rain.parquet", body=(tmp_path / "rain.parquet").read_bytes(), content_type="application/octet-stream")

    seen_headers = {}
    fake_request = web._request
    def recording_request(url, headers, max_bytes, out):
        seen_headers[url] = dict(headers)
        return fake_request(url, headers, max_bytes, out)
    monkeypatch.setattr(web, "_request", recording_request)

    profile = web.hf_get_dataset("me/rain", max_rows=3000)
    assert profile["rows"] == 3000 and profile["path"].endswith(".parquet")
    assert seen_headers[file_url]["Authorization"] == "Bearer secret"
    assert "Authorization" not in seen_headers["https://cdn.example/rain.parquet"] # the token stays with Hugging Face
    assert list(web._data_dir().glob("*.download")) == []
