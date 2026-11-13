# Web tools

Tools that let a Cornilia model collect data from the web: fetch a page and read its main text, pull out its tables, build a small dataset from what it reads, download data files, clean them, and get datasets from Hugging Face.

They are made for small local models, such as Qwen3.5 35B-A3B (about 3B active parameters) through Ollama. A model that size is good at picking a tool and filling in a small JSON object. It is bad at cleaning data, at long reasoning and at keeping track of large tool results, and it will make up values or drop rows without saying so. So these tools give it as little to do as possible.

They live in `examples/` and not in the core because they are one integration among many. Copy `web.py` into your own project's tools folder to use it there.

## Install

```bash
pip install -e ".[web]"          # httpx, trafilatura, pandas, pyarrow, pydantic, lxml, openpyxl
pip install -e ".[web-search]"   # optional, only for the web_search tool
```

Without these packages the tools still load, and a call tells the model the exact `pip` command for the user.

## Run it

```bash
CORNILIA_TOOLS_DIR=examples/web_tools CORNILIA_TRACE=1 python -m cornilia
```

Together with the GitHub tools, by listing both folders (`:` on macOS and Linux, `;` on Windows):

```bash
CORNILIA_TOOLS_DIR=examples/web_tools:examples/github_tools python -m cornilia
```

On Windows, set the same in your `configs.py`: `TOOLS_DIR = ["examples/web_tools", "examples/github_tools"]`.

Then ask something like:

- "Find a Hugging Face dataset about Lebanese weather and save a 500-row sample."
- "Fetch https://en.wikipedia.org/wiki/Climate_of_Lebanon and save its tables."
- "Read this page and make a dataset of every city with its population: <link>."

Every file goes to `./cornilia_data` (or `CORNILIA_DATA_DIR`): datasets at the top, fetched pages in `pages/`, and the fields of each dataset made with `create_dataset` in `schemas/`.

## Settings

| Environment variable | Default | What it does |
| --- | --- | --- |
| `CORNILIA_DATA_DIR` | `./cornilia_data` | Where every file is written. The tools never write outside it, and refuse names with `..`, slashes or an absolute path |
| `WEB_TOOLS_MAX_CHARS` | `2000` | The most text a tool sends back to the model. Longer text is cut into parts, and the result says how to read the next one |
| `WEB_TOOLS_MAX_BYTES` | `20000000` (about 20 MB) | The largest download |
| `WEB_TOOLS_ALLOWED_DOMAINS` | none | Optional comma separated allow list, such as `wikipedia.org,data.gov`. When set, other sites are refused. Subdomains are allowed |
| `WEB_TOOLS_RESPECT_ROBOTS` | on | Checks each site's `robots.txt` before fetching. Set to `0` to turn it off |
| `WEB_TOOLS_DELAY` | `1` | Seconds to wait between two requests to the same site |
| `HF_TOKEN` | none | Optional Hugging Face token, for gated datasets and a higher rate limit. Only sent to Hugging Face |
| `HF_ENDPOINT` | `https://huggingface.co` | The Hugging Face Hub, for a mirror |

Safety: only `http` and `https` links are followed. A link, or a redirect, to a private, loopback or link-local address (such as `127.0.0.1`, `10.x.x.x` or `169.254.169.254`) is refused. Every request has a timeout and a size limit, every request says who it is with a `cornilia-web-tools` User-Agent, and downloaded content is only parsed, never run. The address check happens before the request, so a site that changes its DNS answer between the check and the request (DNS rebinding) is not covered; use `WEB_TOOLS_ALLOWED_DOMAINS` when that matters.

## Tools

| Tool | What it does |
| --- | --- |
| `fetch_page` | Downloads a page, keeps only its main text (no menus, ads or footers) and saves it. Returns the title, the first part of the text, the number of parts and how many tables the page has |
| `read_page` | Returns the next part of a fetched page, so a long page is read piece by piece |
| `extract_tables` | Saves every HTML table of a page as CSV, with `pandas.read_html`, and returns a profile of each |
| `create_dataset` | Starts a dataset with fixed fields: name -> `text`, `integer`, `number`, `date`, `boolean` or `url` |
| `save_rows` | Checks rows against those fields with pydantic, converts types, trims text, skips duplicates and saves the valid rows. Says why each other row was rejected |
| `download_dataset` | Downloads a CSV, JSON, JSONL, Parquet or XLSX file, detects its format and returns a profile |
| `clean_dataset` | Applies cleaning steps chosen from a fixed list and saves a new file. Returns rows, columns and missing values before and after |
| `profile_dataset` | Describes any saved dataset |
| `hf_search_datasets` | Searches the Hugging Face Hub: id, description, downloads, last update, size and tasks |
| `hf_get_dataset` | Saves rows of a Hugging Face dataset (a sample through the rows API, or the parquet export for more than 2,000 rows). Lists the configs and splits when the ones given don't exist |
| `web_search` | Optional. Searches the web with the `ddgs` package and returns titles, links and snippets |

A profile is: the dataset name and path, the row count, each column with its type and % missing, and up to five sample rows with long cells cut. It is kept under `WEB_TOOLS_MAX_CHARS`.

The cleaning steps of `clean_dataset`, applied in the order given:

| Step | What it does |
| --- | --- |
| `normalize_column_names` | `" City Name "` becomes `city_name`, duplicates get `_2` |
| `strip_whitespace` | Trims text cells, and an empty cell becomes missing |
| `drop_empty_rows` | Drops rows with no values |
| `drop_duplicate_rows` | Drops exact duplicate rows |
| `coerce_numbers` | Turns a text column into numbers (`"1,200"`, `"$5"`, `"12%"`) when 90% of its values are numbers |
| `parse_dates` | Turns a text column into dates when 90% of its values are dates |
| `drop_constant_columns` | Drops columns with the same value in every row |

## Why the code cleans and not the model

Ask a small model to read a table and write it back cleaned, and it will quietly round numbers, skip rows that were hard to read, and fill gaps with values that look right. The result looks fine and is wrong. So here the model only makes choices, and Python does the exact work:

- **Choices, not work.** The model picks a link, a dataset, a name, and cleaning steps from a fixed list. Fetching, removing boilerplate, parsing tables, converting types, dropping duplicates and checking rows are plain code that does the same thing every time.
- **Data on disk, not in the chat.** A table of 5,000 rows never goes into the conversation. The model sees its name, row count, columns and a few sample rows, and works with the name.
- **Rows are checked, never guessed.** When the model reads data from text, it first fixes the fields with `create_dataset`. `save_rows` refuses a row with a wrong type or an unknown field and says why, so the model fixes that row instead of the code guessing. A value the model doesn't know is left out and saved as missing.
- **Errors say what to do next.** "No table found on this page; use read_page to read the text, then create_dataset and save_rows." Cornilia gives the error to the model, and a small model follows a plain next step well.

For example, "Find a Hugging Face dataset about Lebanese weather and save a 500-row sample" becomes `hf_search_datasets(query="weather lebanon")`, then `hf_get_dataset(dataset_id=..., max_rows=500)`, and maybe `clean_dataset(name=..., operations=["normalize_column_names", "drop_duplicate_rows"])`. Three short calls with small arguments, and every row in the file came from the source.
