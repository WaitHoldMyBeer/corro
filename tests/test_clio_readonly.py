"""Clio is input only: nothing but GET may reach the Clio API.

Two halves. The static half reads every Python file in the repository and
refuses any HTTP library outside the two files allowed to speak to Clio, and
any write verb inside them other than the declared OAuth token exchange. The
run-time half drives the real client against a fake transport and proves the
guard fires before a request leaves.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent

CLIENT = "server/clio/client.py"
OAUTH = "server/clio/oauth.py"

# Libraries that can open a connection. Importing one anywhere else is a finding:
# either route the call through the Clio client or justify a new entry here.
NETWORK_MODULES = {
    "httpx", "requests", "aiohttp", "urllib3", "http.client", "urllib.request", "socket", "pycurl",
    "websockets", "ftplib", "smtplib",
}
# Files allowed to import a network library, and why.
NETWORK_ALLOWED = {
    CLIENT: "the GET-only Clio client",
    OAUTH: "the OAuth token exchange (decision 12)",
}
# Shelling out is another way to reach a host.
PROCESS_MODULES = {"subprocess", "os.system", "pty"}
WRITE_VERBS = {"post", "put", "patch", "delete"}
VERB_TAKING = {"request", "stream", "build_request", "send"}


def python_files() -> list[str]:
    def git(*args: str) -> list[str]:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False).stdout.splitlines()

    names = set(git("ls-files", "*.py")) | set(git("ls-files", "-o", "--exclude-standard", "*.py"))
    return sorted(n for n in names if not n.startswith("tests/") and (ROOT / n).is_file())


def imported_modules(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def uses(modules: set[str], watched: set[str]) -> set[str]:
    return {m for m in modules for w in watched if m == w or m.startswith(w + ".")}


def only_exception_types(tree: ast.AST, network: set[str]) -> bool:
    """True when a file touches a network library solely to name its exception
    classes (to catch a transport error raised by the Clio client)."""
    roots = {module.split(".")[0] for module in network}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in roots:
            if not all(alias.name.endswith("Error") for alias in node.names):
                return False
        if isinstance(node, ast.Name) and node.id in roots and not isinstance(node.ctx, ast.Load):
            return False
    uses_of_root = [
        node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in roots
    ]
    attribute_values = {
        id(node.value): node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in roots
    }
    return all(attribute_values.get(id(node), "").endswith("Error") for node in uses_of_root)


def calls(tree: ast.AST) -> list[tuple[ast.Call, str]]:
    return [
        (node, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]


def test_only_the_clio_package_can_open_a_connection() -> None:
    offenders = []
    for name in python_files():
        tree = ast.parse((ROOT / name).read_text(), name)
        modules = imported_modules(tree)
        network = uses(modules, NETWORK_MODULES)
        if network and name not in NETWORK_ALLOWED and not only_exception_types(tree, network):
            offenders.append(f"{name} imports {', '.join(sorted(network))}")
        if name.startswith("server/clio/") and uses(modules, PROCESS_MODULES):
            offenders.append(f"{name} can start a process")
    assert not offenders, (
        "network access outside the audited files (an LLM SDK is fine; a raw HTTP library is not):\n  "
        + "\n  ".join(offenders)
    )


def test_no_process_is_spawned_with_a_url() -> None:
    """curl or wget from a subprocess would walk around the guard."""
    offenders = []
    for name in python_files():
        text = (ROOT / name).read_text()
        modules = imported_modules(ast.parse(text, name))
        if uses(modules, PROCESS_MODULES) and any(tool in text for tool in ("curl", "wget", "http://", "https://")):
            offenders.append(name)
    assert not offenders, f"files that spawn processes and mention a URL or a download tool: {offenders}"


def test_client_file_has_no_write_verb() -> None:
    tree = ast.parse((ROOT / CLIENT).read_text(), CLIENT)
    problems = []
    for node, attr in calls(tree):
        if attr in WRITE_VERBS:
            problems.append(f"{CLIENT}:{node.lineno} calls .{attr}()")
        if attr in VERB_TAKING:
            verb = node.args[0] if node.args else None
            if not (isinstance(verb, ast.Constant) and verb.value == "GET"):
                problems.append(f"{CLIENT}:{node.lineno} calls .{attr}() with a verb that is not the literal 'GET'")
        if attr in ("Client", "AsyncClient") and not any(kw.arg == "event_hooks" for kw in node.keywords):
            problems.append(f"{CLIENT}:{node.lineno} builds an HTTP client without the read-only request hook")
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else []
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr == "event_hooks":
                problems.append(f"{CLIENT}:{node.lineno} reassigns event_hooks")
    assert not problems, "\n".join(problems)


def test_oauth_file_sends_exactly_one_post_and_only_to_the_token_endpoint() -> None:
    tree = ast.parse((ROOT / OAUTH).read_text(), OAUTH)
    writes = [(node, attr) for node, attr in calls(tree) if attr in WRITE_VERBS | VERB_TAKING]
    assert len(writes) == 1, f"{OAUTH} must contain exactly one outbound call, found {len(writes)}"
    node, attr = writes[0]
    assert attr == "post"
    url = node.args[0]
    assert isinstance(url, ast.JoinedStr), "the token URL must be built in place so it can be read here"
    tail = url.values[-1]
    assert isinstance(tail, ast.Constant) and tail.value == "/oauth/token", (
        f"{OAUTH}:{node.lineno} posts somewhere other than /oauth/token"
    )
    assert not any(attr in ("Client", "AsyncClient") for _, attr in calls(tree)), (
        f"{OAUTH} must not hold a reusable HTTP client"
    )


# -- run time ----------------------------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.url.path.endswith("/download"):
            return httpx.Response(303, headers={"location": "https://storage.invalid/signed"})
        if request.url.host == "storage.invalid":
            return httpx.Response(200, content=b"synthetic bytes")
        return httpx.Response(200, json={"data": [{"id": 1}], "meta": {"paging": {}}})


@pytest.fixture
def client_and_recorder():
    from server.clio.client import ClioClient

    recorder = Recorder()
    client = ClioClient("https://clio.invalid/api/v4", lambda: "synthetic-token", transport=httpx.MockTransport(recorder))
    yield client, recorder
    client.close()


@pytest.mark.parametrize("verb", ["POST", "PUT", "PATCH", "DELETE", "post", "OPTIONS", "HEAD"])
def test_every_http_client_inside_the_clio_client_refuses_non_get(client_and_recorder, verb: str) -> None:
    from server.clio.client import ClioWriteForbidden

    client, recorder = client_and_recorder
    inner = [value for value in vars(client).values() if isinstance(value, (httpx.Client, httpx.AsyncClient))]
    assert inner, "expected the Clio client to hold its HTTP clients as attributes"
    for http in inner:
        with pytest.raises(ClioWriteForbidden):
            http.request(verb, "https://clio.invalid/api/v4/notes.json", json={"data": {}})
    assert recorder.seen == [], "a refused request still reached the transport"


def test_public_reads_send_only_get(client_and_recorder, tmp_path: Path) -> None:
    client, recorder = client_and_recorder
    client.get("matters/1.json", {"fields": "id"})
    list(client.get_all("notes.json", {"matter_id": 1}))
    client.download("documents/1/download", tmp_path / "doc.bin")
    assert recorder.seen, "the fake transport saw nothing"
    assert {request.method for request in recorder.seen} == {"GET"}
    assert (tmp_path / "doc.bin").read_bytes() == b"synthetic bytes"


def test_storage_host_never_receives_the_clio_token(client_and_recorder, tmp_path: Path) -> None:
    client, recorder = client_and_recorder
    client.download("documents/1/download", tmp_path / "doc.bin")
    to_storage = [request for request in recorder.seen if request.url.host == "storage.invalid"]
    assert to_storage and all("authorization" not in request.headers for request in to_storage)


def test_client_class_has_no_public_method_that_takes_a_verb() -> None:
    import inspect

    from server.clio.client import ClioClient

    for name, member in inspect.getmembers(ClioClient, inspect.isfunction):
        if name.startswith("_"):
            continue
        parameters = set(inspect.signature(member).parameters)
        assert not parameters & {"method", "verb", "json", "data", "content", "files"}, (
            f"ClioClient.{name} accepts {parameters}: a read-only client has no body and no verb argument"
        )


# Modules that may talk to the Clio package at all. Everything else (upload, the assistant, the
# graph, the dashboard, the checker, sharing) works from our own database and has no path to Clio.
CLIO_CALLERS = {"server/app.py", "server/cli.py", "server/sync.py"}


def test_only_the_sync_path_can_reach_the_clio_package() -> None:
    offenders = []
    for name in python_files():
        if name.startswith("server/clio/") or name in CLIO_CALLERS:
            continue
        tree = ast.parse((ROOT / name).read_text(), name)
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = ("." * node.level) + (node.module or "")
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            reaches = "clio" in module.split(".") or any(n == "clio" or n.startswith("server.clio") for n in names)
            uses_sync = module.endswith("sync") and any(n in ("make_client", "sync_matter", "list_matters") for n in names)
            if reaches or uses_sync:
                offenders.append(f"{name}:{node.lineno} imports {module or names}")
    assert not offenders, "modules outside the sync path that can reach Clio:\n  " + "\n  ".join(offenders)
