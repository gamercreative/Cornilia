# GitHub tools

Tools that let a Cornilia model work with GitHub: read repositories, files, commits, issues and pull requests, and, when you turn it on, open issues, comment, create branches, commit files and open pull requests.

They live in `examples/` and not in the core because they are one integration among many. Cornilia's core only knows the general `Tool` interface, and this folder plugs into it like any project's tools would. Copy `github.py` into your own project's tools folder to use it there.

They use the GitHub REST API through the Python standard library, so there is nothing extra to install, and they work with any model Cornilia can talk to.

## Run it

```bash
export GITHUB_TOKEN=<your token>          # optional for public repositories, needed for private ones and for search
CORNILIA_TOOLS_DIR=examples/github_tools CORNILIA_TRACE=1 python -m cornilia
```

Then ask something like "What are the open issues in octocat/Hello-World?".

On Windows, set the same values in your `configs.py`, for example `TOOLS_DIR = "examples/github_tools"` and `os.environ.setdefault("GITHUB_TOKEN", "...")`.

## Settings

| Environment variable | Default | What it does |
| --- | --- | --- |
| `GITHUB_TOKEN` (or `GH_TOKEN`) | none | A [personal access token](https://github.com/settings/personal-access-tokens). A fine-grained token limited to the repositories you want is the safest choice |
| `GITHUB_TOOLS_ALLOW_WRITE` | off | Set to `1` to let the model change things on GitHub. Without it, the write tools refuse and tell the model why |
| `GITHUB_TOOLS_REPOS` | none | Comma separated allow list, such as `me/app,me/docs`. When set, every other repository is refused |
| `GITHUB_API_URL` | `https://api.github.com` | For GitHub Enterprise, `https://<host>/api/v3` |

The token is only read from the environment and never written anywhere.

## Tools

| Tool | What it does | Writes |
| --- | --- | --- |
| `github_get_repo` | Description, default branch, language, stars | |
| `github_list_files` | Files and folders in one folder | |
| `github_read_file` | Text of one file (long files are cut at 20,000 characters) | |
| `github_search_code` | Code search, optionally in one repository (needs a token) | |
| `github_list_branches` | Branches | |
| `github_list_commits` | Latest commits, optionally for one path | |
| `github_list_issues` | Issues, by state and labels | |
| `github_get_issue` | One issue or pull request conversation with its comments | |
| `github_list_pull_requests` | Pull requests, by state | |
| `github_get_pull_request` | One pull request with the files it changes | |
| `github_triage_issues` | Checks many issues against a list of rules and plans, or applies, labels, comments and closing | only with `apply` |
| `github_create_issue` | Opens an issue | yes |
| `github_comment` | Comments on an issue or pull request | yes |
| `github_update_issue` | Changes title, text, labels or open/closed state | yes |
| `github_create_branch` | Creates a branch | yes |
| `github_write_file` | Creates or replaces one file on a branch, as one commit | yes |
| `github_create_pull_request` | Opens a pull request | yes |

A typical change made by the model is `github_create_branch`, then `github_write_file` for each file, then `github_create_pull_request`. There is no merge or delete tool on purpose, so a person always reviews and merges.

## Triage with rules

`github_triage_issues` does in one call what would otherwise take many: it lists the newest issues, checks each one against a list of rules, and returns a plan of what each rule would do. Each rule is a nested object with a `match` (labels, title text, author, age, comment count) and an `action` (labels to add or remove, a comment, closing). It only reads unless the model passes `apply: true`, and applying also needs `GITHUB_TOOLS_ALLOW_WRITE=1`.

Try it with a request such as "In octocat/Hello-World, plan this triage: issues with no labels and at least 30 days old get the label stale, and issues whose title mentions crash get the label bug. Don't change anything."

Its arguments look like this, which makes it a good test of how well a model handles nested tool arguments:

```json
{
  "repo": "octocat/Hello-World",
  "rules": [
    {"name": "stale", "match": {"labels_none": ["bug", "enhancement"], "min_age_days": 30}, "action": {"add_labels": ["stale"]}},
    {"name": "crash", "match": {"title_contains": "crash"}, "action": {"add_labels": ["bug"]}}
  ],
  "limit": 50
}
```
