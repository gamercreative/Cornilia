"""
GitHub tools for Cornilia: let a model read repositories, files, issues and pull requests,
and (when you allow it) open issues, comment, create branches, commit files and open pull requests.

Everything goes through the GitHub REST API with the Python standard library, so there is nothing
extra to install, and the tools work with any model Cornilia talks to.

Settings, all read from environment variables (or set them in os.environ from your configs.py):

    GITHUB_TOKEN                 a personal access token. GH_TOKEN works too. Without one, only public
                                 repositories can be read, with a low rate limit
    GITHUB_TOOLS_ALLOW_WRITE     set to 1 to turn on the tools that change things on GitHub. Off by default,
                                 so a model can never post or push unless you said so
    GITHUB_TOOLS_REPOS           optional comma separated allow list such as "me/app,me/docs". When set, the
                                 tools refuse any other repository
    GITHUB_API_URL               defaults to https://api.github.com. Point it at GitHub Enterprise's
                                 https://<host>/api/v3 if you use that

Every tool takes the repository as "owner/name". A failed call raises an error, and Cornilia hands
the error text back to the model so it can correct itself.
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Annotated, Literal

from cornilia.tools import tool

Repo = Annotated[str, "The repository as owner/name, for example octocat/Hello-World"]

MAX_FILE_CHARS = 20000  # a long file is cut so it doesn't fill the model's context
MAX_BODY_CHARS = 4000   # the same for issue, pull request and comment bodies


# ---------- settings and the one function that talks to GitHub ----------

def _env_flag(name:str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")

def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None

def _check_repo(repo:str) -> str:
    repo = repo.strip().strip("/")
    if repo.count("/") != 1 or not all(repo.split("/")):
        raise ValueError(f"repository must look like owner/name, got {repo!r}")
    allowed = [r.strip().lower() for r in os.environ.get("GITHUB_TOOLS_REPOS", "").split(",") if r.strip()]
    if allowed and repo.lower() not in allowed:
        raise PermissionError(f"{repo} is not in GITHUB_TOOLS_REPOS, allowed repositories: {allowed}")
    return repo

def _check_write():
    if not _env_flag("GITHUB_TOOLS_ALLOW_WRITE"):
        raise PermissionError("writing to GitHub is turned off. The user can turn it on with GITHUB_TOOLS_ALLOW_WRITE=1")
    if not _token():
        raise PermissionError("writing to GitHub needs a token in GITHUB_TOKEN")

# sends one request to the GitHub API and returns the decoded JSON
def _api(method:str, path:str, params:dict | None = None, body:dict | None = None, raw:bool = False):
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})

    headers = {
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "cornilia-github-tools",
    }
    if _token():
        headers["Authorization"] = f"Bearer {_token()}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read()).get("message", "")
        except Exception:
            message = ""
        hint = " (is GITHUB_TOKEN set and allowed to see this repository?)" if e.code in (401, 403, 404) else ""
        raise RuntimeError(f"GitHub returned {e.code} for {method} {path}: {message}{hint}") from None

    if raw:
        return content.decode("utf-8", errors="replace")
    return json.loads(content) if content else {}

def _cut(text:str | None, limit:int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n... [cut, {len(text) - limit} more characters]"

# GitHub answers with big objects, the model only needs a few fields of each
def _issue_summary(item:dict) -> dict:
    return {
        "number": item["number"],
        "title": item["title"],
        "state": item["state"],
        "author": (item.get("user") or {}).get("login"),
        "labels": [label["name"] for label in item.get("labels", [])],
        "comments": item.get("comments"),
        "is_pull_request": "pull_request" in item,
        "url": item.get("html_url"),
    }


# ---------- reading ----------

@tool
def github_get_repo(repo:Repo) -> dict:
    """Gets a GitHub repository's description, default branch, language, stars and open issue count."""
    data = _api("GET", f"/repos/{_check_repo(repo)}")
    return {
        "full_name": data["full_name"],
        "description": data.get("description"),
        "default_branch": data["default_branch"],
        "language": data.get("language"),
        "stars": data.get("stargazers_count"),
        "open_issues": data.get("open_issues_count"),
        "private": data.get("private"),
        "url": data.get("html_url"),
    }

@tool
def github_list_files(repo:Repo,
                      path:Annotated[str, "Folder inside the repository, empty for the top level"] = "",
                      ref:Annotated[str | None, "Branch, tag or commit, leave out for the default branch"] = None) -> list:
    """Lists the files and folders in one folder of a GitHub repository."""
    data = _api("GET", f"/repos/{_check_repo(repo)}/contents/{urllib.parse.quote(path.strip('/'))}", {"ref": ref})
    if isinstance(data, dict): # the path is a file, not a folder
        return [{"name": data["name"], "path": data["path"], "type": data["type"], "size": data.get("size")}]
    return [{"name": item["name"], "path": item["path"], "type": item["type"], "size": item.get("size")} for item in data]

@tool
def github_read_file(repo:Repo,
                     path:Annotated[str, "Path of the file inside the repository, for example src/app.py"],
                     ref:Annotated[str | None, "Branch, tag or commit, leave out for the default branch"] = None) -> dict:
    """Reads the text of one file in a GitHub repository."""
    text = _api("GET", f"/repos/{_check_repo(repo)}/contents/{urllib.parse.quote(path.strip('/'))}", {"ref": ref}, raw=True)
    return {"path": path, "ref": ref, "content": _cut(text, MAX_FILE_CHARS)}

@tool
def github_search_code(query:Annotated[str, "Words to search for"],
                       repo:Annotated[str | None, "Limit the search to this owner/name repository"] = None) -> list:
    """Searches code on GitHub and returns the matching files. Needs a token."""
    q = f"{query} repo:{_check_repo(repo)}" if repo else query
    data = _api("GET", "/search/code", {"q": q, "per_page": 20})
    return [{"repo": item["repository"]["full_name"], "path": item["path"], "url": item["html_url"]} for item in data.get("items", [])]

@tool
def github_list_branches(repo:Repo) -> list:
    """Lists the branches of a GitHub repository."""
    data = _api("GET", f"/repos/{_check_repo(repo)}/branches", {"per_page": 100})
    return [{"name": b["name"], "sha": b["commit"]["sha"]} for b in data]

@tool
def github_list_commits(repo:Repo,
                        ref:Annotated[str | None, "Branch, tag or commit to start from, leave out for the default branch"] = None,
                        path:Annotated[str | None, "Only commits that changed this file or folder"] = None,
                        limit:Annotated[int, "How many commits, at most 50"] = 10) -> list:
    """Lists the latest commits of a GitHub repository, newest first."""
    data = _api("GET", f"/repos/{_check_repo(repo)}/commits", {"sha": ref, "path": path, "per_page": min(max(limit, 1), 50)})
    return [{
        "sha": c["sha"][:12],
        "message": c["commit"]["message"].split("\n")[0],
        "author": c["commit"]["author"]["name"],
        "date": c["commit"]["author"]["date"],
    } for c in data]

@tool
def github_list_issues(repo:Repo,
                       state:Literal["open", "closed", "all"] = "open",
                       labels:Annotated[str | None, "Comma separated label names to filter by"] = None,
                       limit:Annotated[int, "How many issues, at most 50"] = 20) -> list:
    """Lists issues of a GitHub repository, newest first. Pull requests are left out."""
    data = _api("GET", f"/repos/{_check_repo(repo)}/issues", {"state": state, "labels": labels, "per_page": min(max(limit, 1), 50)})
    return [_issue_summary(item) for item in data if "pull_request" not in item]

@tool
def github_get_issue(repo:Repo, number:Annotated[int, "The issue or pull request number"]) -> dict:
    """Reads one GitHub issue or pull request conversation: its text and its comments."""
    repo = _check_repo(repo)
    issue = _api("GET", f"/repos/{repo}/issues/{number}")
    comments = _api("GET", f"/repos/{repo}/issues/{number}/comments", {"per_page": 50})
    return {
        **_issue_summary(issue),
        "body": _cut(issue.get("body"), MAX_BODY_CHARS),
        "comment_list": [{"author": (c.get("user") or {}).get("login"), "body": _cut(c.get("body"), MAX_BODY_CHARS)} for c in comments],
    }

@tool
def github_list_pull_requests(repo:Repo,
                              state:Literal["open", "closed", "all"] = "open",
                              limit:Annotated[int, "How many pull requests, at most 50"] = 20) -> list:
    """Lists pull requests of a GitHub repository, newest first."""
    data = _api("GET", f"/repos/{_check_repo(repo)}/pulls", {"state": state, "per_page": min(max(limit, 1), 50)})
    return [{
        "number": pr["number"],
        "title": pr["title"],
        "state": pr["state"],
        "draft": pr.get("draft"),
        "author": (pr.get("user") or {}).get("login"),
        "head": pr["head"]["ref"],
        "base": pr["base"]["ref"],
        "url": pr["html_url"],
    } for pr in data]

@tool
def github_get_pull_request(repo:Repo, number:Annotated[int, "The pull request number"]) -> dict:
    """Reads one GitHub pull request: its description, branches, merge state and the files it changes."""
    repo = _check_repo(repo)
    pr = _api("GET", f"/repos/{repo}/pulls/{number}")
    files = _api("GET", f"/repos/{repo}/pulls/{number}/files", {"per_page": 100})
    return {
        "number": pr["number"],
        "title": pr["title"],
        "state": pr["state"],
        "merged": pr.get("merged"),
        "mergeable": pr.get("mergeable"),
        "author": (pr.get("user") or {}).get("login"),
        "head": pr["head"]["ref"],
        "base": pr["base"]["ref"],
        "body": _cut(pr.get("body"), MAX_BODY_CHARS),
        "files": [{"path": f["filename"], "status": f["status"], "additions": f["additions"], "deletions": f["deletions"]} for f in files],
        "url": pr["html_url"],
    }


# ---------- writing, only when GITHUB_TOOLS_ALLOW_WRITE=1 ----------

@tool
def github_create_issue(repo:Repo,
                        title:Annotated[str, "Short title of the issue"],
                        body:Annotated[str, "The issue text, Markdown is fine"] = "",
                        labels:Annotated[list[str] | None, "Label names to add"] = None) -> dict:
    """Opens a new issue in a GitHub repository."""
    _check_write()
    payload = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    issue = _api("POST", f"/repos/{_check_repo(repo)}/issues", body=payload)
    return {"number": issue["number"], "url": issue["html_url"]}

@tool
def github_comment(repo:Repo,
                   number:Annotated[int, "The issue or pull request number"],
                   body:Annotated[str, "The comment text, Markdown is fine"]) -> dict:
    """Adds a comment to a GitHub issue or pull request."""
    _check_write()
    comment = _api("POST", f"/repos/{_check_repo(repo)}/issues/{number}/comments", body={"body": body})
    return {"id": comment["id"], "url": comment["html_url"]}

@tool
def github_update_issue(repo:Repo,
                        number:Annotated[int, "The issue or pull request number"],
                        title:Annotated[str | None, "New title, leave out to keep it"] = None,
                        body:Annotated[str | None, "New text, leave out to keep it"] = None,
                        state:Literal["open", "closed"] | None = None,
                        labels:Annotated[list[str] | None, "Replaces all labels with these, leave out to keep them"] = None) -> dict:
    """Changes a GitHub issue or pull request: its title, text, labels, or closes and reopens it."""
    _check_write()
    payload = {key: value for key, value in {"title": title, "body": body, "state": state, "labels": labels}.items() if value is not None}
    if not payload:
        raise ValueError("nothing to change, give at least one of title, body, state or labels")
    issue = _api("PATCH", f"/repos/{_check_repo(repo)}/issues/{number}", body=payload)
    return _issue_summary(issue)

@tool
def github_create_branch(repo:Repo,
                         branch:Annotated[str, "Name of the new branch"],
                         from_ref:Annotated[str | None, "Branch to start from, leave out for the default branch"] = None) -> dict:
    """Creates a new branch in a GitHub repository."""
    _check_write()
    repo = _check_repo(repo)
    from_ref = from_ref or _api("GET", f"/repos/{repo}")["default_branch"]
    sha = _api("GET", f"/repos/{repo}/git/ref/heads/{urllib.parse.quote(from_ref)}")["object"]["sha"]
    _api("POST", f"/repos/{repo}/git/refs", body={"ref": f"refs/heads/{branch}", "sha": sha})
    return {"branch": branch, "from": from_ref, "sha": sha}

@tool
def github_write_file(repo:Repo,
                      path:Annotated[str, "Path of the file inside the repository"],
                      content:Annotated[str, "The full new text of the file"],
                      message:Annotated[str, "The commit message"],
                      branch:Annotated[str, "Branch to commit to. Use a new branch, not the default one"]) -> dict:
    """Creates or replaces one file on a branch of a GitHub repository, as one commit."""
    _check_write()
    repo = _check_repo(repo)
    file_path = urllib.parse.quote(path.strip("/"))
    payload = {"message": message, "content": base64.b64encode(content.encode()).decode(), "branch": branch}
    try: # replacing a file needs the sha of the version being replaced
        payload["sha"] = _api("GET", f"/repos/{repo}/contents/{file_path}", {"ref": branch})["sha"]
    except RuntimeError as e:
        if "returned 404" not in str(e):
            raise
    result = _api("PUT", f"/repos/{repo}/contents/{file_path}", body=payload)
    return {"path": path, "branch": branch, "commit": result["commit"]["sha"][:12], "created": "sha" not in payload}

@tool
def github_create_pull_request(repo:Repo,
                               title:Annotated[str, "Title of the pull request"],
                               head:Annotated[str, "The branch with the changes"],
                               base:Annotated[str | None, "The branch to merge into, leave out for the default branch"] = None,
                               body:Annotated[str, "Description of the changes, Markdown is fine"] = "",
                               draft:bool = False) -> dict:
    """Opens a pull request in a GitHub repository."""
    _check_write()
    repo = _check_repo(repo)
    base = base or _api("GET", f"/repos/{repo}")["default_branch"]
    pr = _api("POST", f"/repos/{repo}/pulls", body={"title": title, "head": head, "base": base, "body": body, "draft": draft})
    return {"number": pr["number"], "url": pr["html_url"]}
