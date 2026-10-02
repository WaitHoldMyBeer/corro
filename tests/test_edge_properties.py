"""Loops over made-up input: what must hold whatever the policy, the model or the archive says.

- A policy with any mix of categories, hidden ids and approvals never puts a
  never-shared category, another provider's item or a firm link in the view.
- Whatever operations a model proposes when customising a share, the policy
  that results stays inside the shareable categories.
- Whatever a zip member is called, a file that is kept has a name and a folder
  that are labels and not paths, and nothing is written outside the data folder.

The seed is fixed, so a failure repeats.
"""

from __future__ import annotations

import io
import random
import string
import time
import zipfile

import pytest
import test_provider_projection as projection
from test_new_shell_server import synthetic_pdf
from test_provider_routes import MATTER, client  # noqa: F401  (client is a fixture)
from test_share_customise import op, run

from server import ingest, share
from shared import contract as c

SHAREABLE, FIRM_ONLY, CASE = projection.SHAREABLE, projection.FIRM_ONLY, projection.CASE
ROUNDS = 300
JUNK = ["", "nope", "everything", "*", "strategy ", "STATUS", "status,strategy", "valuation\x00"]


def item_ids() -> list[str]:
    """Every id the synthetic case holds for either provider, plus ids that are nobody's."""
    ids = []
    for viewer in projection.PROVIDERS:
        own = share.own_item_ids(CASE, viewer)
        ids += [item for group in own for item in group]
        ids += [ask.id for ask in next(p for p in CASE.providers if p.contact.id == viewer).asks]
    return sorted(set(ids)) + ["", "nope", "ask:task:999", "bill:0", "x" * 500]


def random_policy(rng: random.Random, viewer: int) -> c.SharePolicy:
    """A policy row as it could sit in the database, valid or not: built without validation on purpose."""
    pool = SHAREABLE + FIRM_ONLY + JUNK
    ids = item_ids()
    return c.SharePolicy.model_construct(
        contact_id=viewer,
        allowed_categories=rng.sample(pool, rng.randint(0, len(pool))) + rng.choices(pool, k=rng.randint(0, 3)),
        hidden_item_ids=rng.sample(ids, rng.randint(0, len(ids))),
        approved_asks={ask_id: projection.APPROVED for ask_id in rng.sample(ids, rng.randint(0, len(ids)))},
        message=None,
        updated_at=None,
    )


@pytest.mark.parametrize("viewer", projection.PROVIDERS)
def test_no_policy_however_malformed_shares_a_never_shared_category(viewer: int) -> None:
    rng = random.Random(20311 + viewer)
    own_asks = {ask.id for ask in next(p for p in CASE.providers if p.contact.id == viewer).asks}
    failures = []
    for _ in range(ROUNDS):
        policy = random_policy(rng, viewer)
        view = share.provider_view(CASE, viewer, policy)
        allowed = set(policy.allowed_categories) & set(SHAREABLE)
        found = projection.leaks(view, viewer, allowed)
        if not {ask.id for ask in view.asks} <= own_asks:
            found.append("a request that is not this provider's")
        if any(ask.id in policy.hidden_item_ids for ask in view.asks):
            found.append("a request the attorney removed")
        if found:
            failures.append((policy.allowed_categories, found))
    assert not failures, f"{len(failures)} of {ROUNDS} policies leak; first: {failures[0]}"


def random_operation(rng: random.Random, ids: list[str]) -> dict:
    verbs = ["set_category", "hide_item", "restore_item", "reword_ask", "set_cover_note", "reorder_sections", "send_now", "share_everything", "", "set_policy"]
    categories = SHAREABLE + FIRM_ONLY + JUNK
    return op(
        rng.choice(verbs),
        category=rng.choice(categories + [None]),
        on=rng.choice([True, False, None]),
        id=rng.choice(ids + [None]),
        text=rng.choice([None, "", "Synthetic wording for the request.", "x" * 2000]),
        sections=rng.choice([None, [], rng.sample(categories, rng.randint(0, len(categories)))]),
    )


def test_no_list_of_proposed_operations_takes_a_policy_outside_the_allowlist() -> None:
    rng = random.Random(20312)
    ids = item_ids()
    for _ in range(ROUNDS):
        proposed = [random_operation(rng, ids) for _ in range(rng.randint(0, 8))]
        policy, applied, refused = run(proposed)
        assert set(policy.allowed_categories) <= set(SHAREABLE), f"{proposed} left the policy with {policy.allowed_categories}"
        assert len(applied) + len(refused) <= len(proposed) + 1
        view = share.provider_view(CASE, policy.contact_id, policy)
        found = projection.leaks(view, policy.contact_id, set(policy.allowed_categories))
        assert not found, f"{proposed} leaks {found}"


# --------------------------------------------------------------------------- zip member names

PIECES = ["..", ".", "", "a", "folder", "Imported", "C:", "c:", "~", "~$lock", ".hidden", "__MACOSX", "Thumbs.db", "con", "n" * 300, "sp ace", "\u202e", "x.pdf", "x.txt", "x.zip", "..pdf", "...", "x.PDF "]
SEPARATORS = ["/", "\\", "//", "\\\\", "/./", "/../"]


def random_member(rng: random.Random) -> str:
    parts = [rng.choice(PIECES) if rng.random() < 0.8 else "".join(rng.choices(string.ascii_letters + " ._-", k=rng.randint(1, 12))) for _ in range(rng.randint(1, 6))]
    name = "".join(part + rng.choice(SEPARATORS) for part in parts[:-1]) + parts[-1]
    return rng.choice(["", "/", "\\", "C:\\", "//host/share/"]) + name if rng.random() < 0.3 else name


def archive_of(members: list[tuple[str, bytes]]) -> bytes:
    held = io.BytesIO()
    with zipfile.ZipFile(held, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in members:
            archive.writestr(name, body)
    return held.getvalue()


def test_no_member_name_is_kept_as_a_path() -> None:
    if not hasattr(ingest, "zip_entries"):
        pytest.skip("the zip import is not in this build")
    rng = random.Random(20313)
    names = sorted({random_member(rng) for _ in range(2000)} - {""})
    kept = 0
    for start in range(0, len(names), 100):
        batch = [name for name in names[start : start + 100] if not name.endswith(("/", "\\"))]
        entries = ingest.zip_entries(zipfile.ZipFile(io.BytesIO(archive_of([(name, b"x") for name in batch]))))
        for entry in entries:
            if entry["skip"] is not None:
                continue
            kept += 1
            # The folder is a label: one name, or nested names joined with " / ". Each part must be a plain name.
            for label in (entry["name"], *entry["folder"].split(" / ")):
                assert label and "/" not in label and "\\" not in label and label not in ("..", "."), f"{entry['path']!r} is kept as {label!r}"
                assert len(label) <= 255
            assert ".." not in entry["path"].split("/") and not entry["path"].startswith("/"), f"{entry['path']!r} is kept"
    assert kept > 50, "the loop kept almost nothing, so it proves nothing"


def test_a_hostile_archive_is_imported_inside_the_data_folder_and_says_what_it_left_out(tmp_path, monkeypatch, client) -> None:  # noqa: F811
    if "/api/matters/{matter_id}/import/zip" not in client.app.openapi()["paths"]:
        pytest.skip("the zip import is not mounted in this build")
    good = synthetic_pdf(["Synthetic sheet kept from the archive."])
    escapes = ["../../escape.pdf", "/abs/escape.pdf", "C:\\win\\escape.pdf", "folder/../../escape.pdf", "..\\..\\escape.pdf"]
    members = [(name, synthetic_pdf([f"Synthetic escape {n}."])) for n, name in enumerate(escapes)]
    members += [("kept/ok.pdf", good), ("copy/ok-again.pdf", good), ("kept/words.txt", b"Synthetic words in a text file."), ("kept/empty.pdf", b""),
                ("kept/.hidden", b"x"), ("kept/inner.zip", b"PK"), ("kept/odd.bin", b"\x00\x01\x02"), ("kept/broken.pdf", b"%PDF-1.4 broken"), ("n" * 300 + ".pdf", synthetic_pdf(["Synthetic long name."]))]
    link = zipfile.ZipInfo("kept/link.pdf")
    link.external_attr = 0o120777 << 16  # a symbolic link, as a zip made on a unix machine records it
    members += [(link, b"/etc/hostname"), (".", b"a member named with one dot")]
    base = f"/api/matters/{MATTER}"
    for body in (b"", b"plain words", archive_of([]), archive_of([("folder/", b"")]), archive_of([("big.txt", b"0" * 120_000_000)])):
        refused = client.post(f"{base}/import/zip", content=body, headers={"content-type": "application/zip"})
        assert 400 <= refused.status_code < 500 and refused.json().get("detail"), f"{len(body)} bytes answered {refused.status_code}"
    assert client.post("/api/matters/999999/import/zip", content=archive_of(members), headers={"content-type": "application/zip"}).status_code == 404
    accepted = client.post(f"{base}/import/zip", content=archive_of(members), headers={"content-type": "application/zip"})
    assert accepted.status_code == 202, accepted.text[:200]
    job = accepted.json()
    for _ in range(150):
        job = client.get(f"{base}/imports/{job['id']}").json()
        if job["files"]["total"] and job["files"]["done"] == job["files"]["total"] and job["state"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    by_path = {entry["path"].replace("\\", "/"): entry for entry in job["items"]}
    assert job["files"]["total"] == len(members) == len(job["items"]), "a file of the archive is not accounted for"
    for name in escapes:
        entry = by_path[name.replace("\\", "/")]
        assert entry["outcome"] == "skipped" and entry["document_id"] is None and entry["reason"], f"{name} was not refused"
    for name in ("kept/empty.pdf", "kept/.hidden", "kept/inner.zip", "kept/link.pdf", "."):
        assert by_path[name]["outcome"] == "skipped" and by_path[name]["reason"], name
    assert by_path["kept/ok.pdf"]["outcome"] == "stored"
    assert by_path["copy/ok-again.pdf"]["document_id"] == by_path["kept/ok.pdf"]["document_id"], "the same file twice in one archive became two documents"
    data = next(path for path in tmp_path.rglob("swans.db")).parent
    written = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert all(data in path.parents for path in written), "the import wrote outside the data folder"
    assert not [path for path in written if "escape" in path.name], "a refused member was written"
    assert client.get(f"{base}/imports/{job['id']}".replace(f"/{MATTER}/", "/999999/")).status_code == 404
    listed = client.get(f"{base}/records/documents", params={"limit": 200}).json()
    names = [str(item.get("name") or item.get("title")) for item in listed["items"]]
    assert not [name for name in names if "/" in name or "\\" in name or "escape" in name], names
